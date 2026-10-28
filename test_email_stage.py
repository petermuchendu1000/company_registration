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
    saved_get, saved_db, saved_base = requests.get, pipeline.db, pipeline.BASE_DIR
    buf = io.StringIO()
    err = None
    try:
        requests.get = calls
        pipeline.db = _DB(rows)
        if base_dir is not None:
            pipeline.BASE_DIR = Path(base_dir)
        with redirect_stdout(buf):
            pipeline.stage_email(dry_run=dry_run)
    except Exception as e:                      # noqa: BLE001 - reported
        err = e
    finally:
        requests.get = saved_get
        pipeline.db = saved_db
        pipeline.BASE_DIR = saved_base
    return calls, buf.getvalue(), err


def _co(cn="00000001", domain="acme.uk", email="owner@real.example"):
    return {"Company Number": cn, "Domain": domain, "Assigned Email": email}


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


def check_finding_dropped_forward(r: Results) -> None:
    print("\n-- FINDING 1: a forward with an empty field is dropped --")
    # pipeline.py:225 keeps only entries where BOTH mailbox and target are
    # non-empty. Because the set call replaces the entire set, a kept-out
    # entry is DELETED from the live configuration. Whether Namecheap ever
    # returns such a shape (a catch-all, say) is not established here - that
    # would need the live API, which this suite deliberately does not call.
    calls, out, err = run_stage(
        ONE, forwarding_xml(("", "catchall@real.example"),
                            ("info", "i@real.example")))
    if not r.check("exactly one set call", len(calls.sets) == 1, f"{calls.sets}"):
        return
    p = calls.sets[0]
    submitted = {p[f"mailbox{i}"] for i in range(1, 9) if f"mailbox{i}" in p}
    r.check("the well-formed forward is kept", "info" in submitted,
            f"submitted {submitted}")
    r.check("FINDING: the empty-mailbox entry is NOT re-submitted, so the "
            "set call would delete it", "" not in submitted,
            f"submitted {submitted} - if the empty entry now survives, this "
            "finding is fixed; update the check and the docstring")
    r.check("the dropped entry is not reported to the operator",
            "catchall" not in out,
            f"a warning now mentions it - finding fixed; update this check")


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

    calls, out, err = run_stage(ONE, "this is not xml")
    r.check("malformed XML propagates rather than being ignored",
            err is not None and calls.sets == [],
            f"err={err!r} sets={calls.sets}")

    calls, out, err = run_stage(
        ONE, forwarding_xml(),
        get_raises=requests.exceptions.ConnectTimeout("timeout"))
    r.check("a connection timeout propagates and sets nothing",
            isinstance(err, requests.exceptions.ConnectTimeout)
            and calls.sets == [], f"err={err!r} sets={calls.sets}")


def check_partial_completion(r: Results) -> None:
    print("\n-- a failure midway leaves earlier companies already changed --")
    rows = [_co("00000001", "first.uk"), _co("00000002", "second.uk")]
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

    r.check("the second company's bad response raises", err is not None,
            f"err={err!r}")
    r.check("the FIRST company was already modified before the raise",
            len(calls.sets) == 1
            and calls.sets[0].get("DomainName") == "first.uk",
            f"sets={calls.sets}")
    r.check("no summary line is printed, so the run looks aborted",
            "Companies with domains: 2" in buf.getvalue(),
            f"out {buf.getvalue()!r}")


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
def check_manifest(r: Results) -> None:
    print("\n-- FINDING 2: the manifest path is one nothing writes --")
    tmp = tempfile.mkdtemp()
    try:
        # Where stage_email looks (pipeline.py:184,247).
        looks = Path(tmp) / "pipeline_output" / "apps" / "00000001"
        # Where the build actually writes (assets.py:858 via generate._co_dir).
        writes = (Path(tmp) / "pipeline_output" / "companies"
                  / "00000001 - ACME LTD" / "app")
        writes.mkdir(parents=True)
        (writes / "manifest.json").write_text(
            json.dumps({"support_email": "stale@real.example"}), encoding="utf-8")

        calls, out, err = run_stage(ONE, forwarding_xml(), base_dir=tmp)
        r.check("raised nothing", err is None, repr(err))
        r.check("FINDING: the manifest the build wrote is NOT updated",
                json.loads((writes / "manifest.json").read_text())
                ["support_email"] == "stale@real.example",
                "it was updated - the paths now agree; update this check")
        r.check("and nothing is said about the manifest at all",
                "manifest" not in out.lower(), f"out {out!r}")

        # Control: at the path stage_email DOES look at, the update works.
        # Without this the finding could be 'the update is simply broken'.
        looks.mkdir(parents=True)
        (looks / "manifest.json").write_text(
            json.dumps({"support_email": "stale@real.example"}), encoding="utf-8")
        calls, out, err = run_stage(ONE, forwarding_xml(), base_dir=tmp)
        r.check("at pipeline_output/apps/<cn>/ the update DOES happen",
                json.loads((looks / "manifest.json").read_text())
                ["support_email"] == "dev@acme.uk",
                f"out {out!r}")
        r.check("and it is reported", "UPD" in out, f"out {out!r}")

        # Already correct -> left alone, and said so.
        calls, out, err = run_stage(ONE, forwarding_xml(("dev", "x@real.example")),
                                    base_dir=tmp)
        r.check("an already-correct manifest is not rewritten",
                "manifest already has" in out, f"out {out!r}")

        # dry_run must not write the manifest either.
        (looks / "manifest.json").write_text(
            json.dumps({"support_email": "stale@real.example"}), encoding="utf-8")
        calls, out, err = run_stage(ONE, forwarding_xml(), dry_run=True,
                                    base_dir=tmp)
        r.check("dry_run leaves the manifest untouched",
                json.loads((looks / "manifest.json").read_text())
                ["support_email"] == "stale@real.example", f"out {out!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    check_finding_dropped_forward(r)
    check_already_configured(r)
    check_provider_errors(r)
    check_partial_completion(r)
    check_row_filter(r)
    check_dev_address(r)
    check_manifest(r)
    check_no_db_write(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
