#!/usr/bin/env python3
"""Tests for stage 3, dev@ email forwarding (pipeline.py:179-258).

    python3 test_email_stage.py

Hermetic: no network, no database, no Namecheap account, and no writes
inside the repository - BASE_DIR is redirected to a temporary directory for
the manifest checks. stage_email imports requests inside its own body
(pipeline.py:181) so patching the module's `get` is enough, and db is a
module-scope import (pipeline.py:30) so pipeline.db is replaceable. No
production code was changed to make this testable.

This stage mutates live email routing for a real domain. It does not spend
money, but a wrong write silently redirects or destroys a customer's mail,
so the checks that matter most are: dry_run issues no setEmailForwarding
call at all, and an existing forward is never dropped from the set call.

Namecheap's setEmailForwarding REPLACES the whole forwarding set - the code
re-submits `existing + [("dev", ...)]` at pipeline.py:230-237 - so anything
missing from `existing` is deleted. pipeline.py:225 filters to entries where
BOTH mailbox and target are non-empty, which is where FINDING 1 below comes
from.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

# Read with bracket access at pipeline.py:190-193, so a missing one raises.
os.environ.setdefault("NAMECHEAP_API_USER", "test-user")
os.environ.setdefault("NAMECHEAP_API_KEY", "test-key")
os.environ.setdefault("NAMECHEAP_USERNAME", "test-user")
os.environ.setdefault("NAMECHEAP_CLIENT_IP", "127.0.0.1")
os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

import requests
import pipeline

U = "http://api.namecheap.com/xml.response"
GET_CMD = "namecheap.domains.dns.getEmailForwarding"
SET_CMD = "namecheap.domains.dns.setEmailForwarding"


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
        return bool(cond)

    def report(self) -> int:
        print()
        print(f"{self.passed} passed / {len(self.failed)} failed")
        for f in self.failed:
            print(f"  failed: {f}")
        return 1 if self.failed else 0


def forwarding_xml(*pairs: tuple[str, str]) -> str:
    fws = "".join(f'<Forward mailbox="{m}">{t}</Forward>' for m, t in pairs)
    return (f'<ApiResponse Status="OK" xmlns="{U}"><CommandResponse>'
            f'<DomainDNSGetEmailForwardingResult>{fws}'
            '</DomainDNSGetEmailForwardingResult>'
            '</CommandResponse></ApiResponse>')


def error_xml(*messages: str) -> str:
    errs = "".join(f'<Error Number="1">{m}</Error>' for m in messages)
    return (f'<ApiResponse Status="ERROR" xmlns="{U}">'
            f'<Errors>{errs}</Errors></ApiResponse>')


OK_SET = f'<ApiResponse Status="OK" xmlns="{U}"><CommandResponse/></ApiResponse>'


class _Calls:
    """Records every requests.get and answers by Command."""

    def __init__(self, get_body: str, set_body: str = OK_SET,
                 get_raises: Exception | None = None):
        self.get_body = get_body
        self.set_body = set_body
        self.get_raises = get_raises
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params=None, **kw):
        params = params or {}
        cmd = params.get("Command", "")
        self.calls.append((cmd, dict(params)))
        if cmd == GET_CMD and self.get_raises is not None:
            raise self.get_raises
        body = self.set_body if cmd == SET_CMD else self.get_body

        class _R:
            text = body
        return _R()

    @property
    def sets(self):
        return [p for c, p in self.calls if c == SET_CMD]

    @property
    def gets(self):
        return [p for c, p in self.calls if c == GET_CMD]


class _DB:
    def __init__(self, rows): self._rows = rows
    def all_rows(self, use_cache=True): return [dict(r) for r in self._rows]
    def update_row(self, cn, updates): raise AssertionError(
        "stage_email must not write to the database")


def run_stage(rows, get_body, set_body=OK_SET, dry_run=False,
              get_raises=None, base_dir=None):
    calls = _Calls(get_body, set_body, get_raises)
    saved = (requests.get, pipeline.db, pipeline.BASE_DIR,
             pipeline.COMPANIES_DIR)
    buf = io.StringIO()
    err = None
    try:
        requests.get = calls
        pipeline.db = _DB(rows)
        if base_dir is not None:
            # COMPANIES_DIR is bound at import from BASE_DIR, so patching
            # BASE_DIR alone does NOT redirect it - a check of mine passed
            # for that exact wrong reason. Both are patched together.
            pipeline.BASE_DIR = Path(base_dir)
            pipeline.COMPANIES_DIR = (Path(base_dir) / "pipeline_output"
                                      / "companies")
        with redirect_stdout(buf):
            pipeline.stage_email(dry_run=dry_run)
    except Exception as e:                      # noqa: BLE001 - reported
        err = e
    finally:
        (requests.get, pipeline.db, pipeline.BASE_DIR,
         pipeline.COMPANIES_DIR) = saved
    return calls, buf.getvalue(), err


def _co(cn="00000001", name="ACME LTD", domain="acme.uk",
        email="owner@real.example"):
    # Company Name matters: the manifest lives under _co_dir(cn, name), so a
    # row without it resolves to the bare company number and finds nothing.
    return {"Company Number": cn, "Company Name": name,
            "Domain": domain, "Assigned Email": email}


ONE = [_co()]


# ── the production-safety invariant ─────────────────────────────────────
def check_dry_run(r: Results) -> None:
    print("\n-- dry_run must issue no setEmailForwarding call --")
    calls, out, err = run_stage(ONE, forwarding_xml(("info", "i@real.example")),
                                dry_run=True)
    r.check("dry_run raised nothing", err is None, repr(err))
    r.check("dry_run issued ZERO set calls", calls.sets == [],
            f"set calls: {calls.sets}")
    r.check("dry_run still read the current forwards", len(calls.gets) == 1,
            f"get calls: {len(calls.gets)}")
    r.check("dry_run reported what it would do", "DRY" in out, f"out {out!r}")

    # Control: the same input without dry_run MUST issue a set call, or the
    # check above passes for the wrong reason.
    calls2, _, _ = run_stage(ONE, forwarding_xml(("info", "i@real.example")))
    r.check("the same input DOES set when dry_run is off",
            len(calls2.sets) == 1,
            f"set calls: {calls2.sets}; if empty the dry_run check is vacuous")


# ── never destroy an existing forward ───────────────────────────────────
def check_preserves_existing(r: Results) -> None:
    print("\n-- existing forwards survive the replace-the-whole-set call --")
    existing = (("info", "i@real.example"), ("sales", "s@real.example"))
    calls, out, err = run_stage(ONE, forwarding_xml(*existing))
    r.check("raised nothing", err is None, repr(err))
    if not r.check("exactly one set call", len(calls.sets) == 1,
                   f"{calls.sets}"):
        return
    p = calls.sets[0]
    pairs = {}
    i = 1
    while f"mailbox{i}" in p:
        pairs[p[f"mailbox{i}"]] = p[f"ForwardTo{i}"]
        i += 1
    r.check("both existing mailboxes are re-submitted",
            pairs.get("info") == "i@real.example"
            and pairs.get("sales") == "s@real.example",
            f"submitted {pairs}")
    r.check("dev is added", pairs.get("dev") == "owner@real.example",
            f"submitted {pairs}")
    r.check("nothing else is submitted", set(pairs) == {"info", "sales", "dev"},
            f"submitted {sorted(pairs)}")
    r.check("the target domain is carried on the set call",
            p.get("DomainName") == "acme.uk", f"DomainName={p.get('DomainName')!r}")


def check_malformed_forward_refused(r: Results) -> None:
    print("\n-- a forward with an empty field makes the domain refuse --")
    # setEmailForwarding replaces the whole set, so an entry that cannot be
    # re-submitted faithfully would be DELETED. The old code filtered such
    # entries out and submitted the remainder, deleting them silently. It now
    # refuses the domain. A missing dev@ forward is recoverable by re-running;
    # someone else's deleted mail routing is not.
    for label, pairs in (
            ("empty mailbox (a catch-all shape)",
             (("", "catchall@real.example"), ("info", "i@real.example"))),
            ("empty target",
             (("info", ""), ("sales", "s@real.example"))),
            ("both empty", (("", ""),)),
    ):
        calls, out, err = run_stage(ONE, forwarding_xml(*pairs))
        r.check(f"{label}: raised nothing", err is None, repr(err))
        r.check(f"{label}: NOTHING is submitted, so nothing is deleted",
                calls.sets == [], f"set calls: {calls.sets}")
        r.check(f"{label}: the refusal is explained", "SKIP" in out
                and "empty mailbox or target" in out, f"out {out!r}")
        r.check(f"{label}: the offending entries are shown",
                "Refusing to rewrite" in out, f"out {out!r}")

    # Control: well-formed forwards must still be processed, or "nothing is
    # submitted" above would pass for every input and prove nothing.
    calls, out, err = run_stage(
        ONE, forwarding_xml(("info", "i@real.example")))
    r.check("a well-formed set is still processed", len(calls.sets) == 1,
            f"set calls: {calls.sets}; if empty the refusals above are vacuous")


# ── idempotency ─────────────────────────────────────────────────────────
def check_already_configured(r: Results) -> None:
    print("\n-- a dev forward that already exists is left alone --")
    calls, out, err = run_stage(
        ONE, forwarding_xml(("dev", "owner@real.example"),
                            ("info", "i@real.example")))
    r.check("raised nothing", err is None, repr(err))
    r.check("no set call is issued", calls.sets == [], f"{calls.sets}")
    r.check("reported as already set up", "already set up" in out, f"out {out!r}")

    # Idempotent regardless of where dev points: the check at :227 tests the
    # mailbox name only. Pinned so a change to that is deliberate.
    calls2, out2, _ = run_stage(
        ONE, forwarding_xml(("dev", "somewhere-else@real.example")))
    r.check("a dev forward pointing elsewhere is still left alone",
            calls2.sets == [], f"{calls2.sets}")


# ── provider errors ─────────────────────────────────────────────────────
def check_provider_errors(r: Results) -> None:
    print("\n-- the provider refuses, errors, or answers rubbish --")
    calls, out, err = run_stage(ONE, error_xml("Domain is not using Namecheap DNS"))
    r.check("an external-DNS error skips without setting",
            err is None and calls.sets == [] and "SKIP" in out,
            f"err={err!r} sets={calls.sets} out={out!r}")

    calls, out, err = run_stage(ONE, error_xml("Invalid request IP"))
    r.check("any other error is reported without setting",
            err is None and calls.sets == [] and "ERROR" in out,
            f"err={err!r} sets={calls.sets} out={out!r}")
    r.check("the error text is shown to the operator",
            "Invalid request IP" in out, f"out {out!r}")

    calls, out, err = run_stage(ONE, forwarding_xml(), set_body=error_xml("Boom"))
    r.check("a failing set call is reported, not swallowed",
            err is None and "ERROR setting forwarding" in out, f"out {out!r}")

    # These used to propagate and abort the stage. They are now reported
    # per company and the run continues, matching what an API-reported error
    # two lines earlier in the code already did.
    calls, out, err = run_stage(ONE, "this is not xml")
    r.check("malformed XML is reported, not raised",
            err is None and calls.sets == [], f"err={err!r} sets={calls.sets}")
    r.check("the parse failure names the exception type",
            "ParseError" in out and "ERROR reading forwards" in out,
            f"out {out!r}")

    calls, out, err = run_stage(
        ONE, forwarding_xml(),
        get_raises=requests.exceptions.ConnectTimeout("timeout"))
    r.check("a connection timeout is reported and sets nothing",
            err is None and calls.sets == [], f"err={err!r} sets={calls.sets}")
    r.check("the timeout names the exception type",
            "ConnectTimeout" in out, f"out {out!r}")

    # The guard is narrow on purpose. An unexpected exception type inside it
    # means a fault in this code, not a provider hiccup, and must propagate
    # instead of being logged per domain and skipped. Widening the catch to
    # bare Exception survived this suite until this check existed - the
    # credential case below does NOT distinguish them, because _api() is
    # evaluated outside the try and so aborts either way.
    calls, out, err = run_stage(ONE, forwarding_xml(),
                                get_raises=ValueError("a bug, not the network"))
    r.check("an unexpected exception type still propagates",
            isinstance(err, ValueError) and calls.sets == [],
            f"err={err!r} sets={calls.sets}; a bare `except Exception` would "
            "swallow this and report it as a per-domain failure")

    # A missing credential aborts too, for its own reason.
    saved = os.environ.pop("NAMECHEAP_API_KEY")
    try:
        calls, out, err = run_stage(ONE, forwarding_xml())
        r.check("a missing credential aborts rather than being skipped",
                isinstance(err, KeyError), f"err={err!r}")
    finally:
        os.environ["NAMECHEAP_API_KEY"] = saved
    calls, _, err = run_stage(ONE, forwarding_xml())
    r.check("credentials restored for later checks",
            err is None and len(calls.sets) == 1, f"err={err!r}")


def check_partial_completion(r: Results) -> None:
    print("\n-- a failure midway leaves earlier companies already changed --")
    # Keywords, not positions: `name` was added as the second parameter of
    # _co, which silently turned "first.uk" into a company name and left both
    # rows on the default domain.
    rows = [_co(cn="00000001", domain="first.uk"),
            _co(cn="00000002", domain="second.uk")]
    # Good for the first company, unparseable for the second.
    state = {"n": 0}
    good = forwarding_xml(("info", "i@real.example"))

    calls = _Calls(good)
    orig_call = calls.__call__

    def seq(url, params=None, **kw):
        params = params or {}
        if params.get("Command") == GET_CMD:
            state["n"] += 1
            if state["n"] == 2:
                calls.calls.append((GET_CMD, dict(params)))

                class _Bad:
                    text = "<<<not xml"
                return _Bad()
        return orig_call(url, params=params, **kw)

    saved_get, saved_db = requests.get, pipeline.db
    buf, err = io.StringIO(), None
    try:
        requests.get = seq
        pipeline.db = _DB(rows)
        with redirect_stdout(buf):
            pipeline.stage_email(dry_run=False)
    except Exception as e:                      # noqa: BLE001
        err = e
    finally:
        requests.get = saved_get
        pipeline.db = saved_db

    out = buf.getvalue()
    r.check("one bad company no longer aborts the run", err is None,
            f"err={err!r}")
    r.check("the first company was still configured",
            len(calls.sets) == 1
            and calls.sets[0].get("DomainName") == "first.uk",
            f"sets={calls.sets}")
    r.check("the second company is reported as failed, not silent",
            "ERROR reading forwards" in out, f"out {out!r}")
    r.check("the summary accounts for both",
            "1 forward(s) created" in out and "1 failed" in out,
            f"out {out!r}")
    r.check("and states how many were considered",
            "of 2 considered" in out, f"out {out!r}")


# ── which companies are considered ──────────────────────────────────────
def check_row_filter(r: Results) -> None:
    print("\n-- a company needs a number, a domain AND an email --")
    cases = [
        ("no number", _co(cn="")),
        ("no domain", _co(domain="")),
        ("no email", _co(email="")),
    ]
    for label, row in cases:
        calls, out, err = run_stage([row], forwarding_xml())
        r.check(f"{label}: nothing is contacted",
                err is None and calls.calls == [],
                f"calls={calls.calls}")
    calls, out, err = run_stage([_co()], forwarding_xml())
    r.check("a complete row IS processed", len(calls.gets) == 1,
            f"gets={len(calls.gets)}; if 0 the checks above are vacuous")


def check_dev_address(r: Results) -> None:
    print("\n-- the dev mailbox is derived from the domain, not the email --")
    calls, out, err = run_stage(
        [_co(domain="weird-domain.co.uk", email="someone@elsewhere.test")],
        forwarding_xml())
    if not r.check("one set call", len(calls.sets) == 1, f"{calls.sets}"):
        return
    p = calls.sets[0]
    r.check("the mailbox is 'dev'", p.get("mailbox1") == "dev",
            f"mailbox1={p.get('mailbox1')!r}")
    r.check("it forwards to the ASSIGNED email, not dev@domain",
            p.get("ForwardTo1") == "someone@elsewhere.test",
            f"ForwardTo1={p.get('ForwardTo1')!r}")
    r.check("the printed address is dev@<domain>",
            "dev@weird-domain.co.uk" in out, f"out {out!r}")


# ── the manifest update ─────────────────────────────────────────────────
def _fresh_manifest(p: Path, email: str = "stale@real.example") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"support_email": email}), encoding="utf-8")


def check_manifest(r: Results) -> None:
    print("\n-- the manifest the BUILD wrote is the one updated --")
    tmp = tempfile.mkdtemp()
    try:
        po = Path(tmp) / "pipeline_output"
        # Where the build writes: assets.py:858 via generate._co_dir, which is
        # COMPANIES_DIR/<cn> - <name>/app/. _co() supplies the name.
        built = po / "companies" / "00000001 - ACME LTD" / "app" / "manifest.json"
        # The path this stage used to read, which nothing has ever written.
        stale = po / "apps" / "00000001" / "manifest.json"
        _fresh_manifest(built)
        _fresh_manifest(stale)

        calls, out, err = run_stage(ONE, forwarding_xml(), base_dir=tmp)
        r.check("raised nothing", err is None, repr(err))
        r.check("the build's manifest IS updated",
                json.loads(built.read_text())["support_email"] == "dev@acme.uk",
                f"still {json.loads(built.read_text())!r}; out {out!r}")
        r.check("and the update is reported", "UPD" in out, f"out {out!r}")
        r.check("the stale pipeline_output/apps path is NOT touched",
                json.loads(stale.read_text())["support_email"]
                == "stale@real.example",
                "the old path is still being written")

        # A row with no company name resolves to the bare number, so it must
        # not silently update some other company's manifest.
        bare = po / "companies" / "00000001" / "app" / "manifest.json"
        _fresh_manifest(built)
        _fresh_manifest(bare)
        calls, out, err = run_stage([_co(name="")], forwarding_xml(),
                                    base_dir=tmp)
        r.check("a nameless row updates <cn>/, not <cn> - <name>/",
                json.loads(bare.read_text())["support_email"] == "dev@acme.uk"
                and json.loads(built.read_text())["support_email"]
                == "stale@real.example",
                f"bare={json.loads(bare.read_text())!r} "
                f"named={json.loads(built.read_text())!r}")

        # Already correct -> left alone, and said so.
        _fresh_manifest(built, "dev@acme.uk")
        calls, out, err = run_stage(ONE, forwarding_xml(("dev", "x@real.example")),
                                    base_dir=tmp)
        r.check("an already-correct manifest is not rewritten and says so",
                "manifest already has" in out, f"out {out!r}")

        # dry_run must not write the manifest either.
        _fresh_manifest(built)
        calls, out, err = run_stage(ONE, forwarding_xml(), dry_run=True,
                                    base_dir=tmp)
        r.check("dry_run leaves the manifest untouched",
                json.loads(built.read_text())["support_email"]
                == "stale@real.example", f"out {out!r}")
        r.check("dry_run still reports what it would change",
                "DRY" in out and "support_email" in out, f"out {out!r}")

        # A missing manifest must not raise - the build may not have run.
        shutil.rmtree(po / "companies")
        calls, out, err = run_stage(ONE, forwarding_xml(), base_dir=tmp)
        r.check("a missing manifest is not an error", err is None, repr(err))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_print_summary(r: Results) -> None:
    print("\n-- print_summary reads what the build wrote --")
    tmp = tempfile.mkdtemp()
    try:
        po = Path(tmp) / "pipeline_output"
        app = po / "companies" / "13510663 - PORTABILITY FIXTURE LTD" / "app"
        (app / "artifacts").mkdir(parents=True)
        # Artifact paths are stored RELATIVE TO app/, as the real build writes
        # them; resolving them against the company directory finds nothing.
        (app / "artifacts" / "a.apk").write_bytes(b"x")
        (app / "manifest.json").write_text(json.dumps({
            "apk": "artifacts/a.apk", "aab": "artifacts/missing.aab",
            "domain": "fixture-example.uk", "privacy_policy_url": None,
            "support_email": "dev@fixture-example.uk"}), encoding="utf-8")
        # stage_director_id writes here, not to pipeline_output/representative_ids.
        idd = po / "companies" / "13510663 - PORTABILITY FIXTURE LTD" / "director_id"
        idd.mkdir(parents=True)
        (idd / "ID Front.jpeg").write_bytes(b"x")

        saved = (pipeline.BASE_DIR, pipeline.COMPANIES_DIR)
        buf = io.StringIO()
        try:
            pipeline.BASE_DIR = Path(tmp)
            pipeline.COMPANIES_DIR = po / "companies"
            with redirect_stdout(buf):
                pipeline.print_summary()
        finally:
            pipeline.BASE_DIR, pipeline.COMPANIES_DIR = saved
        out = buf.getvalue()

        r.check("the company is listed at all",
                "13510663 - PORTABILITY FIXTURE LTD" in out, f"out {out!r}")
        r.check("an artifact that exists reads APK:OK", "APK:OK" in out,
                f"out {out!r}")
        r.check("an artifact that does not exist reads AAB:--",
                "AAB:--" in out, f"out {out!r}")
        r.check("the ID is found under <company>/director_id/",
                "ID:OK" in out, f"out {out!r}")
        r.check("a null privacy_policy_url reads Policy:--",
                "Policy:--" in out, f"out {out!r}")
        r.check("the domain is reported", "Domain:OK" in out, f"out {out!r}")
        r.check("the support email is shown",
                "dev@fixture-example.uk" in out, f"out {out!r}")
        r.check("it no longer claims pipeline_output/apps is missing",
                "No pipeline_output/apps" not in out, f"out {out!r}")

        # An empty companies dir must say so rather than printing nothing.
        shutil.rmtree(po / "companies")
        (po / "companies").mkdir(parents=True)
        buf = io.StringIO()
        saved = (pipeline.BASE_DIR, pipeline.COMPANIES_DIR)
        try:
            pipeline.BASE_DIR = Path(tmp)
            pipeline.COMPANIES_DIR = po / "companies"
            with redirect_stdout(buf):
                pipeline.print_summary()
        finally:
            pipeline.BASE_DIR, pipeline.COMPANIES_DIR = saved
        r.check("an empty companies directory is reported, not silent",
                "holds no manifest.json" in buf.getvalue(),
                f"out {buf.getvalue()!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_co_dir_matches_writer(r: Results) -> None:
    print("\n-- _co_dir agrees with the code that creates the directory --")
    # THE invariant behind both fixes. pipeline._co_dir only finds the
    # manifest if it builds the same folder name as the writer,
    # apps/generator/generate.py:107-108. A mutation replacing pipeline's
    # re.sub with a bare strip SURVIVED this suite until this check existed:
    # no fixture had a name containing the stripped characters, so the two
    # constructions could diverge silently and every manifest lookup would
    # quietly miss.
    import apps.generator.generate as gen

    class _C:
        def __init__(self, n, nm):
            self.company_number, self.company_name = n, nm

    cases = [
        ("13510663", "PORTABILITY FIXTURE LTD"),
        ("00000001", ""),                        # empty -> bare number
        ("00000002", "   "),                     # whitespace only
        ("00000003", 'A/B:C*D?E"F<G>H|I\\J'),    # every stripped character
        ("00000004", "  Leading & trailing  "),
        ("00000005", "Ünïcodé Ltd"),
        ("00000006", "Dash - In - Name Ltd"),
    ]
    saved = pipeline.COMPANIES_DIR
    try:
        pipeline.COMPANIES_DIR = Path(gen.COMPANIES_DIR)
        mismatches = []
        for cn, nm in cases:
            writer = os.path.normpath(gen._co_dir(_C(cn, nm)))
            reader = os.path.normpath(str(pipeline._co_dir(cn, nm) / "app"))
            if writer != reader:
                mismatches.append((nm, writer, reader))
        r.check(f"all {len(cases)} names resolve identically on both sides",
                not mismatches,
                "; ".join(f"{nm!r}: writer={w} reader={rd}"
                          for nm, w, rd in mismatches))
        # And the check must be capable of failing: a deliberately wrong
        # construction has to be caught, or the loop above proves nothing.
        bogus = os.path.normpath(str(saved / "00000003 - A/B" / "app"))
        r.check("the comparison would catch a divergence",
                bogus != os.path.normpath(gen._co_dir(_C("00000003", 'A/B'))),
                "the comparison cannot distinguish a wrong folder name")
    finally:
        pipeline.COMPANIES_DIR = saved


def check_no_db_write(r: Results) -> None:
    print("\n-- the stage never writes to the database --")
    # _DB.update_row raises; reaching it would surface as an error here.
    calls, out, err = run_stage(ONE, forwarding_xml())
    r.check("no db write attempted", err is None, f"err={err!r}")


def main() -> int:
    r = Results()
    print(f"target: {os.path.join(REPO, 'pipeline.py')} stage_email")
    check_dry_run(r)
    check_preserves_existing(r)
    check_malformed_forward_refused(r)
    check_already_configured(r)
    check_provider_errors(r)
    check_partial_completion(r)
    check_row_filter(r)
    check_dev_address(r)
    check_manifest(r)
    check_print_summary(r)
    check_co_dir_matches_writer(r)
    check_no_db_write(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
