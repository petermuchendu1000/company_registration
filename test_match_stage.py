#!/usr/bin/env python3
"""Tests for stage 2, domain matching (pipeline.py:62-160).

    python3 test_match_stage.py

Hermetic: no network, no database, no Namecheap account. stage_match imports
requests inside its own body (pipeline.py:64), so patching the module's
`get` is enough, and db is a module-scope import (pipeline.py:30) so
pipeline.db is replaceable. Nothing in production is modified to make this
testable.

This stage had no test of any kind, and it writes to the companies table
(pipeline.py:146) against a billed Namecheap account, so the check that
matters most is the last one: dry_run must never write.

Previously, when the top-scoring company already held a domain,
pipeline.py:144-153 wrote nothing and said nothing, having already printed
MATCH - the only trace was MATCH lines outnumbering the "N new domain(s)
written" tally. Choosing the winner by score and only then testing
eligibility is deliberate (match_domains.py:182-206 does the same); what was
missing was the message that sibling prints at match_domains.py:211. The
loop-else at pipeline.py:154-167 now prints it, and the checks below hold the
invariant that makes the output self-balancing:

    MATCH count == writes + "Skipped" messages

The message must name the WINNER, not the last company the loop happened to
visit. After a for loop runs to completion its variables hold the final
iteration, so `name` and `existing` would name the wrong company; the code
uses best_name and looks the held domain up by best_cn. A check below pins
that, because getting it wrong would produce a confidently incorrect line
rather than a visible error.
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

# stage_match reads these with bracket access (pipeline.py:110-113), so a
# missing one is a KeyError, not a silent default. Set before importing.
os.environ.setdefault("NAMECHEAP_API_USER", "test-user")
os.environ.setdefault("NAMECHEAP_API_KEY", "test-key")
os.environ.setdefault("NAMECHEAP_USERNAME", "test-user")
os.environ.setdefault("NAMECHEAP_CLIENT_IP", "127.0.0.1")
os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

import requests
import pipeline

NS_URI = "http://api.namecheap.com/xml.response"


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


def _xml(*names: str) -> str:
    doms = "".join(f'<Domain ID="{i}" Name="{n}" />'
                   for i, n in enumerate(names, 1))
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<ApiResponse Status="OK" xmlns="{NS_URI}">'
            f'<CommandResponse><DomainGetListResult>{doms}'
            '</DomainGetListResult></CommandResponse></ApiResponse>')


class _Recorder:
    """Stands in for db. Records writes; serves fixed rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.writes: list[tuple[str, dict]] = []

    def all_rows(self, use_cache: bool = True) -> list[dict]:
        return [dict(r) for r in self._rows]

    def update_row(self, cn: str, updates: dict) -> None:
        self.writes.append((cn, dict(updates)))


def run_stage(domains, rows, dry_run=False, body=None, raises=None):
    """Call the real stage_match with requests and db stubbed."""
    rec = _Recorder(rows)
    text = body if body is not None else _xml(*domains)

    class _Resp:
        pass
    _Resp.text = text

    def fake_get(*a, **k):
        if raises is not None:
            raise raises
        return _Resp()

    saved_get, saved_db = requests.get, pipeline.db
    buf = io.StringIO()
    err = None
    try:
        requests.get = fake_get
        pipeline.db = rec
        with redirect_stdout(buf):
            pipeline.stage_match(dry_run=dry_run)
    except Exception as e:                      # noqa: BLE001 - reported, not hidden
        err = e
    finally:
        requests.get = saved_get
        pipeline.db = saved_db
    out = buf.getvalue()
    return rec.writes, out, err


def _co(cn, name, domain=""):
    return {"Company Number": cn, "Company Name": name, "Domain": domain}


ACME_FREE = _co("00000001", "ACME WIDGETS LTD")
ACME_HELD = _co("00000001", "ACME WIDGETS LTD", "acme-old.uk")
TRADING = _co("00000002", "ACME TRADING LTD")
UNRELATED = _co("00000003", "ZEBRA LOGISTICS LIMITED")
DOM = "acme2widgets.uk"


# ── the money-safety invariant ──────────────────────────────────────────
def check_dry_run(r: Results) -> None:
    print("\n-- dry_run must never write (the invariant that protects the table) --")
    writes, out, err = run_stage([DOM], [ACME_FREE, TRADING], dry_run=True)
    r.check("dry_run raised nothing", err is None, repr(err))
    r.check("dry_run performed ZERO writes", writes == [], f"wrote {writes}")
    r.check("dry_run still reported the match", "MATCH" in out,
            "a dry run that shows nothing is useless for review")
    r.check("dry_run says it did not write", "Dry run" in out,
            f"output was {out!r}")
    # The loop-else at pipeline.py:154 sits inside `if not dry_run`. If its
    # indentation slipped so it paired with that `if` instead of the `for`,
    # a dry run would print a skip message it never evaluated.
    r.check("dry_run prints no skip message", "Skipped" not in out,
            f"output was {out!r}")

    # The same input WITHOUT dry_run must write - otherwise the check above
    # passes for the wrong reason and proves nothing.
    writes2, _, _ = run_stage([DOM], [ACME_FREE, TRADING], dry_run=False)
    r.check("the same input DOES write when dry_run is off",
            len(writes2) == 1,
            f"wrote {writes2}; if this is empty the dry_run check is vacuous")


# ── the write itself ────────────────────────────────────────────────────
def check_write_shape(r: Results) -> None:
    print("\n-- the write targets the right company with the right payload --")
    writes, out, err = run_stage([DOM], [ACME_FREE, TRADING])
    r.check("exactly one write", len(writes) == 1, f"wrote {writes}")
    if len(writes) != 1:
        return
    cn, updates = writes[0]
    r.check("write targets the top scorer", cn == "00000001", f"cn={cn!r}")
    r.check("payload is exactly the Domain header",
            updates == {"Domain": DOM}, f"payload={updates!r}")
    # "Domain" must be a header db.update_row recognises, or the write is a
    # silent no-op (db.py:331-333 skips unknown headers).
    import db as real_db
    r.check("'Domain' is a mapped header in db.HEADER_TO_COL",
            "Domain" in real_db.HEADER_TO_COL,
            "db.update_row would silently drop it")
    r.check("it maps to the 'domain' column",
            real_db.HEADER_TO_COL.get("Domain") == "domain",
            f"maps to {real_db.HEADER_TO_COL.get('Domain')!r}")


# ── no match ────────────────────────────────────────────────────────────
def check_no_match(r: Results) -> None:
    print("\n-- a domain matching nothing is skipped, not written --")
    writes, out, err = run_stage(["totallyunrelated.uk"], [UNRELATED])
    r.check("no write", writes == [], f"wrote {writes}")
    r.check("reported as SKIP", "SKIP" in out, f"output {out!r}")
    r.check("no MATCH claimed", "MATCH" not in out, f"output {out!r}")


# ── boundaries ──────────────────────────────────────────────────────────
def check_boundaries(r: Results) -> None:
    print("\n-- both boundaries of the 0.5 threshold --")
    # Measured, not assumed: ACME TRADING LTD against acme2widgets scores
    # exactly 0.50 - the lowest value _score can return without returning 0.
    writes, out, _ = run_stage([DOM], [TRADING])
    r.check("a score of exactly 0.50 is accepted",
            len(writes) == 1 and "(score=0.50)" in out,
            f"writes={writes} out={out!r}")

    writes, out, _ = run_stage(["zzzqqqxxx.uk"], [UNRELATED])
    r.check("a score of 0.00 is rejected",
            writes == [] and "(best score=0.00)" in out,
            f"writes={writes} out={out!r}")

    # The gate at :141 must not be stricter than the lowest nonzero score
    # _score can produce, or matches vanish with no trace. 60k sampled
    # inputs produced nothing in (0, 0.5) and a minimum nonzero of 0.5.
    src = open(os.path.join(REPO, "pipeline.py"), encoding="utf-8").read()
    r.check("the threshold is still 0.5, matching the lowest nonzero score",
            "if best_score >= 0.5:" in src,
            "if the gate rose above 0.5, word-overlap matches would be "
            "silently discarded; if _score changed, re-derive the minimum")


# ── duplicate / already-occupied ────────────────────────────────────────
def check_occupied(r: Results) -> None:
    print("\n-- a top scorer that already holds a domain --")
    writes, out, err = run_stage([DOM], [ACME_HELD, TRADING])
    r.check("nothing is written", writes == [], f"wrote {writes}")
    r.check("MATCH was nevertheless printed", "MATCH" in out, f"out {out!r}")
    r.check("the tally says zero written", "0 new domain(s)" in out,
            f"out {out!r}")
    r.check("the skip is explained, not silent",
            "Skipped" in out and "already has" in out, f"out {out!r}")
    # The message must identify the WINNER. Using the loop's trailing `name`
    # would print 'ACME TRADING LTD' here - the last company visited - which
    # is wrong and would look authoritative.
    r.check("the message names the winning company, not the last visited",
            "ACME WIDGETS LTD" in out and "ACME TRADING LTD" not in out,
            f"out {out!r}")
    r.check("the message reports the domain actually held",
            "already has: acme-old.uk" in out, f"out {out!r}")
    # And the free lower scorer does NOT receive it. Intended, per
    # match_domains.py:182-206, but worth pinning so a change is deliberate.
    r.check("the free lower scorer is not given the domain instead",
            not any(cn == "00000002" for cn, _ in writes),
            f"wrote {writes}")

    # A successful write must NOT also print a skip: the loop-else fires only
    # when the loop completed without break.
    w2, out2, _ = run_stage([DOM], [ACME_FREE, TRADING])
    r.check("a successful write prints no skip message",
            len(w2) == 1 and "Skipped" not in out2, f"out {out2!r}")


def check_output_invariant(r: Results) -> None:
    print("\n-- every MATCH is accounted for: writes + skips --")
    # Measured: one free company and two matching domains gives 2 MATCH,
    # 1 write, 1 Skipped - the second skip reporting the domain written
    # moments earlier via the in-memory update at pipeline.py:148-151.
    for label, rows, dom_count in (
            ("one free company, two matching domains", [ACME_FREE], 2),
            ("one occupied company, two matching domains", [ACME_HELD], 2),
            ("no match at all", [UNRELATED], 0)):
        writes, out, err = run_stage([DOM, "acme2widgets.co.uk"], rows)
        matches = out.count("MATCH")
        skips = out.count("Skipped")
        r.check(f"{label}: MATCH == writes + Skipped",
                matches == len(writes) + skips,
                f"MATCH={matches} writes={len(writes)} Skipped={skips}")
        r.check(f"{label}: MATCH count is {dom_count}", matches == dom_count,
                f"got {matches}")

    # The second skip must quote the freshly written domain, which only
    # happens if the in-memory list was updated.
    writes, out, _ = run_stage([DOM, "acme2widgets.co.uk"], [ACME_FREE])
    r.check("the second skip quotes the domain just written",
            f"already has: {DOM}" in out, f"out {out!r}")


def check_tie_breaking(r: Results) -> None:
    print("\n-- a tie is broken deterministically by row order --")
    # "LTD" and "LIMITED" are both in _STOPWORDS (pipeline.py:72), so these
    # two names normalise to the same string and necessarily score the same.
    # Which one wins is decided by `score > best_score` at pipeline.py:138
    # being strict, and "first" is well defined because db.all_rows orders
    # by row_no NULLS LAST, company_number (db.py:255).
    #
    # This exists because a mutation flipping :138 to `>=` survived the
    # suite: no other fixture here ties, so last-wins was indistinguishable
    # from first-wins.
    a = _co("00000001", "ACME WIDGETS LTD")
    b = _co("00000002", "ACME WIDGETS LIMITED")
    w1, out1, e1 = run_stage([DOM], [a, b])
    w2, out2, e2 = run_stage([DOM], [b, a])
    r.check("neither order raised", e1 is None and e2 is None,
            f"{e1!r} / {e2!r}")
    r.check("both orders write exactly once",
            len(w1) == 1 and len(w2) == 1, f"{w1} / {w2}")
    r.check("the two names really do score identically",
            "(score=1.00)" in out1 and "(score=1.00)" in out2,
            "if the scores differ this is not a tie and proves nothing")
    if len(w1) == 1 and len(w2) == 1:
        r.check("the FIRST row in db order wins the tie, not the last",
                w1[0][0] == "00000001" and w2[0][0] == "00000002",
                f"A-then-B gave {w1[0][0]!r}, B-then-A gave {w2[0][0]!r}; "
                "last-wins would invert both")


def check_one_domain_per_company(r: Results) -> None:
    print("\n-- one company cannot collect two domains in a single run --")
    writes, out, _ = run_stage(["acme2widgets.uk", "acme2widgets.co.uk"],
                               [ACME_FREE])
    r.check("exactly one write for two matching domains",
            len(writes) == 1, f"wrote {writes}")
    r.check("two MATCH lines but one write",
            out.count("MATCH") == 2 and len(writes) == 1,
            f"MATCH count={out.count('MATCH')} writes={len(writes)}")
    r.check("the tally reports 1, not 2", "1 new domain(s)" in out,
            f"out {out!r}")


# ── the provider misbehaving ────────────────────────────────────────────
def check_provider_failures(r: Results) -> None:
    print("\n-- the provider never responds, or answers rubbish --")
    writes, out, err = run_stage([DOM], [ACME_FREE],
                                 raises=requests.exceptions.ConnectTimeout("timeout"))
    r.check("a connection timeout propagates rather than writing",
            isinstance(err, requests.exceptions.ConnectTimeout) and writes == [],
            f"err={err!r} writes={writes}")

    writes, out, err = run_stage([], [ACME_FREE], body="this is not xml")
    r.check("malformed XML propagates rather than writing",
            err is not None and writes == [], f"err={err!r} writes={writes}")

    writes, out, err = run_stage([], [ACME_FREE],
                                 body=f'<ApiResponse Status="OK" xmlns="{NS_URI}"/>')
    r.check("an empty domain list writes nothing and raises nothing",
            err is None and writes == [], f"err={err!r} writes={writes}")

    # This case was missing from this suite: it covered timeouts and
    # malformed XML, but not the response that is well-formed XML saying the
    # call was refused. An error response carries no Domain elements, so the
    # stage printed "Namecheap domains: []" and "0 new domain(s) written" -
    # the same output as a healthy account with nothing left to match.
    # Stage 1's "Namecheap API: OK" sat directly above it. match_domains.py:118
    # raises on this condition for the identical call.
    err_body = ('<?xml version="1.0" encoding="utf-8"?>'
                f'<ApiResponse Status="ERROR" xmlns="{NS_URI}"><Errors>'
                '<Error Number="2019166">API Key is invalid or API access '
                'has not been enabled</Error></Errors></ApiResponse>')
    writes, out, err = run_stage([], [ACME_FREE], body=err_body)
    r.check("a refused call raises instead of looking like an empty account",
            isinstance(err, RuntimeError), f"err={err!r} out={out!r}")
    r.check("...carrying the API's own message",
            err is not None and "API Key is invalid" in str(err), f"err={err!r}")
    r.check("...and writes nothing", writes == [], f"writes={writes}")
    r.check("...and does not print an empty domain list",
            "Namecheap domains: []" not in out, f"out={out!r}")
    r.check("...and does not report a successful run",
            "new domain(s) written" not in out, f"out={out!r}")

    # A company row with no number or no name must be ignored
    # (pipeline.py:127 requires both).
    writes, out, err = run_stage([DOM], [_co("", "ACME WIDGETS LTD"),
                                         _co("00000009", "")])
    r.check("rows missing a number or a name are ignored",
            err is None and writes == [], f"err={err!r} writes={writes}")
    # Asserting only "wrote nothing" is what let this through. When every
    # usable row is discarded the inner loop never runs, so every domain
    # prints best score=0.00 and the run is indistinguishable from one where
    # the domains genuinely did not match. Observed on production: 35
    # domains, 35 zeros, no way to tell which had happened.
    r.check("...and the operator is told there was nothing to match against",
            "NOTHING TO MATCH AGAINST" in out,
            f"35 zeros with no explanation is not a readable result. {out!r}")
    r.check("...and told the scores are 0.00 for that reason",
            "not because the domains failed to match" in out, f"out={out!r}")


def check_company_count_reported(r: Results) -> None:
    print("\n-- the stage must say how many rows it compared against --")
    writes, out, err = run_stage([DOM], [ACME_FREE, TRADING, UNRELATED])
    r.check("the count of loaded companies is reported",
            "Companies loaded: 3" in out, f"out={out!r}")
    r.check("...split into free and already-set",
            "3 without a domain, 0 already set" in out, f"out={out!r}")

    writes, out, err = run_stage([DOM], [ACME_HELD, TRADING])
    r.check("a held domain counts as already set",
            "Companies loaded: 2" in out and "1 without a domain, 1 already set"
            in out, f"out={out!r}")

    # Rows the loader discards must not be counted, or the number is a
    # comfort rather than a measurement.
    writes, out, err = run_stage([DOM], [ACME_FREE, _co("", "NO NUMBER LTD"),
                                         _co("00000009", "")])
    r.check("discarded rows are not counted",
            "Companies loaded: 1" in out, f"out={out!r}")

    # With rows present the alarm must stay silent, or it means nothing.
    r.check("the empty-table alarm does NOT fire when rows exist",
            "NOTHING TO MATCH AGAINST" not in out, f"out={out!r}")

    writes, out, err = run_stage([DOM], [])
    r.check("an empty table reports zero loaded",
            "Companies loaded: 0" in out, f"out={out!r}")
    r.check("...and raises the alarm",
            "NOTHING TO MATCH AGAINST" in out, f"out={out!r}")
    r.check("...and still writes nothing", writes == [], f"writes={writes}")


def check_missing_credentials(r: Results) -> None:
    print("\n-- a missing credential fails loudly, not silently --")
    saved = os.environ.pop("NAMECHEAP_API_KEY")
    try:
        writes, out, err = run_stage([DOM], [ACME_FREE])
        r.check("a missing NAMECHEAP_API_KEY raises KeyError",
                isinstance(err, KeyError), f"err={err!r}")
        r.check("and writes nothing", writes == [], f"wrote {writes}")
    finally:
        os.environ["NAMECHEAP_API_KEY"] = saved
    # Prove the restore worked, or every later run in this process is void.
    writes, _, err = run_stage([DOM], [ACME_FREE])
    r.check("credentials restored for subsequent checks",
            err is None and len(writes) == 1, f"err={err!r} writes={writes}")


def main() -> int:
    r = Results()
    print(f"target: {os.path.join(REPO, 'pipeline.py')} stage_match")
    check_dry_run(r)
    check_write_shape(r)
    check_no_match(r)
    check_boundaries(r)
    check_occupied(r)
    check_output_invariant(r)
    check_tie_breaking(r)
    check_one_domain_per_company(r)
    check_provider_failures(r)
    check_company_count_reported(r)
    check_missing_credentials(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
