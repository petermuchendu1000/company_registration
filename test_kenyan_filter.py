#!/usr/bin/env python3
"""Only companies with a Kenyan director may enter the pipeline.

    python3 test_kenyan_filter.py

Hermetic: no network, no database. The officer records below are real ones
captured from the live Companies House API, so the rule is exercised
against what the data actually looks like rather than what it ought to.

Why the rule costs what it does. /search/companies carries NO officer data
at all - measured, its item keys are address, address_snippet,
company_number, company_status, company_type, date_of_creation,
description, description_identifier, kind, links, matches, snippet, title -
and /advanced-search/companies silently IGNORES a nationality parameter
(hits came back 20075 with `nationality`, `officer_nationality`,
`director_nationality`, and with none). Nationality exists only on
/company/{cn}/officers, so each candidate costs one extra request against a
budget of 600 per 5 minutes.

The rule: admit iff at least one ACTIVE DIRECTOR is Kenyan AND no active
director's nationality is unrecorded. Both halves were chosen against real
cases, which are the fixtures below.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

import api_v2  # noqa: E402


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


def officer(nat, role="director", resigned=None) -> dict:
    return {"nationality": nat, "officer_role": role, "resigned_on": resigned}


# Captured live. Each name is a real company and each list is its real set
# of active directors' nationality values.
FC009164 = [officer("Canadian"), officer("Kenyan"), officer("Kenyan"),
            officer("Kenyan"), officer("Swedish")]              # KENYA AIRWAYS PLC
FC010541 = [officer("Kenyan")] * 11 + [officer("New Zealander")]  # KENYA FLAMINGO
SC461938 = [officer("British"), officer("British"),
            officer("British,Kenyan"), officer("Greek")]        # KWISA
ACME     = [officer("British")]                                 # ACME ADVISORY LTD
NAIROBI  = [officer("British,Irish"), officer(None)]            # NAIROBI BUSINESS ANGELS


# ── the nationality parser ──────────────────────────────────────────────
def check_parser(r: Results) -> None:
    print("\n-- nationality is free text and must be split on commas only --")
    n = api_v2._nationalities
    r.check("a single value", n(officer("Kenyan")) == ["kenyan"])
    r.check("a dual national splits on the comma",
            n(officer("British,Kenyan")) == ["british", "kenyan"])
    # 'South African' is one nationality containing a space. Splitting on
    # whitespace would invent two, and 'african' is not a nationality.
    r.check("a value containing a space stays one value",
            n(officer("South African")) == ["south african"],
            "splitting on whitespace would invent a nationality")
    r.check("None gives nothing", n(officer(None)) == [])
    r.check("empty gives nothing", n(officer("")) == [])
    r.check("case is normalised", n(officer("KENYAN")) == ["kenyan"])
    r.check("surrounding space is stripped", n(officer("  kenyan  ")) == ["kenyan"])
    r.check("spacing after the comma is tolerated",
            n(officer("British, Kenyan")) == ["british", "kenyan"])
    # Must not match a nationality that merely contains the letters.
    r.check("a value is matched whole, not as a substring",
            "kenyan" not in n(officer("NotKenyanAtAll")),
            "substring matching is the defect already fixed in stage 1")


# ── the admission rule, on real records ─────────────────────────────────
def check_rule(r: Results) -> None:
    print("\n-- the rule, against real Companies House records --")
    v = api_v2._kenyan_verdict

    ok, why = v(FC009164)
    r.check("KENYA AIRWAYS PLC is admitted (Kenyan, Canadian, Swedish)", ok, why)
    r.check("...and says how many", "3 of 5" in why, f"said {why!r}")

    ok, why = v(FC010541)
    r.check("KENYA FLAMINGO is admitted (11 Kenyan, 1 New Zealander)", ok, why)

    ok, why = v(SC461938)
    r.check("a dual national 'British,Kenyan' counts as Kenyan", ok, why)

    ok, why = v(ACME)
    r.check("a British-only company is refused", not ok, why)
    r.check("...and says why", "no Kenyan director" in why, f"said {why!r}")

    # The second half of the rule, and a real case: 2 of 60 sampled
    # directors had no nationality on record.
    ok, why = v(NAIROBI)
    r.check("an unrecorded nationality refuses the company", not ok, why)
    r.check("...and says so specifically",
            "no nationality recorded" in why, f"said {why!r}")

    print("\n-- the edges --")
    r.check("no officers at all is refused", not v([])[0])
    r.check("...and says so", "no active directors" in v([])[1])
    r.check("a resigned Kenyan director does not admit",
            not v([officer("Kenyan", resigned="2020-01-01"),
                   officer("British")])[0],
            "a director who has left cannot make a company eligible")
    r.check("a Kenyan SECRETARY does not admit",
            not v([officer("Kenyan", role="secretary"),
                   officer("British")])[0],
            "the rule is about directors")
    r.check("a company whose only officer is a resigned Kenyan is refused",
            not v([officer("Kenyan", resigned="2020-01-01")])[0])
    r.check("one Kenyan director alone is admitted", v([officer("Kenyan")])[0])
    r.check("role matching is case-insensitive",
            v([officer("Kenyan", role="DIRECTOR")])[0])
    # 'Kenyan' must not be matched inside another word.
    r.check("'South African' alone is refused",
            not v([officer("South African")])[0])
    # The verdict, not just the parser, must compare whole values. Matching
    # 'kenyan' as a substring is the defect already fixed in stage 1, where
    # 'ip' matched inside Description, Multiple and Equipment.
    for bogus in ("NotKenyan", "Kenyanese", "Un-Kenyan"):
        r.check(f"{bogus!r} does not admit a company",
                not v([officer(bogus)])[0],
                "a nationality must match whole, not as a substring")
    r.check("...while 'Kenyan' itself still does",
            v([officer("Kenyan")])[0])


# ── the fetch layer: cache, failure handling, budget ────────────────────
def check_fetch(r: Results) -> None:
    print("\n-- a lookup failure must refuse, never admit --")
    saved_key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    try:
        os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
        api_v2._nat_cache.clear()
        ok, why = api_v2._fetch_kenyan("00000001")
        r.check("no API key refuses rather than admits", not ok, why)
        r.check("...and names the variable",
                "COMPANIES_HOUSE_API_KEY" in why, f"said {why!r}")
        r.check("...and is NOT cached",
                "00000001" not in api_v2._nat_cache,
                "caching a configuration failure would keep refusing a "
                "company that is in fact eligible, long after the fix")
    finally:
        if saved_key is None:
            os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
        else:
            os.environ["COMPANIES_HOUSE_API_KEY"] = saved_key

    print("\n-- a transport failure or an HTTP error must refuse, and not stick --")
    import requests as _rq

    class _Resp:
        def __init__(self, code): self.status_code = code; self.headers = {}
        def json(self): return {}

    saved_get = _rq.get
    saved_key3 = os.environ.get("COMPANIES_HOUSE_API_KEY")
    try:
        os.environ["COMPANIES_HOUSE_API_KEY"] = "test-key"

        # An exception out of requests must not be read as "eligible".
        api_v2._nat_cache.clear()
        _rq.get = lambda *a, **k: (_ for _ in ()).throw(
            _rq.exceptions.ConnectTimeout("down"))
        ok, why = api_v2._fetch_kenyan("00000002")
        r.check("a transport failure refuses rather than admits", not ok, why)
        r.check("...and names the failure", "lookup failed" in why, f"said {why!r}")
        r.check("...and is NOT cached", "00000002" not in api_v2._nat_cache,
                "an outage would otherwise refuse this company for 24 hours")

        # A 429 says nothing about the company either.
        api_v2._nat_cache.clear()
        _rq.get = lambda *a, **k: _Resp(429)
        ok, why = api_v2._fetch_kenyan("00000003")
        r.check("HTTP 429 refuses", not ok, why)
        r.check("...and reports the status", "429" in why, f"said {why!r}")
        r.check("...and is NOT cached", "00000003" not in api_v2._nat_cache,
                "caching a rate-limit response would keep refusing an "
                "eligible company long after the window reset")

        # A 200 verdict IS worth caching.
        api_v2._nat_cache.clear()
        class _Ok(_Resp):
            def json(self): return {"items": [officer("Kenyan")]}
        _rq.get = lambda *a, **k: _Ok(200)
        ok, why = api_v2._fetch_kenyan("00000004")
        r.check("a real verdict is returned", ok, why)
        r.check("...and IS cached", "00000004" in api_v2._nat_cache,
                "without this the cache check below passes for a cache that "
                "is never populated")
    finally:
        _rq.get = saved_get
        if saved_key3 is None:
            os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
        else:
            os.environ["COMPANIES_HOUSE_API_KEY"] = saved_key3
        api_v2._nat_cache.clear()

    print("\n-- the cache answers without a request --")
    api_v2._nat_cache.clear()
    api_v2._nat_cache["12345678"] = (time.time() + 3600, True, "cached yes")
    ok, why = api_v2._fetch_kenyan("12345678")
    r.check("a fresh cache entry is used", ok and why == "cached yes",
            f"got {(ok, why)!r}")
    api_v2._nat_cache["12345678"] = (time.time() - 1, True, "stale yes")
    saved_key2 = os.environ.get("COMPANIES_HOUSE_API_KEY")
    try:
        os.environ.pop("COMPANIES_HOUSE_API_KEY", None)
        ok, why = api_v2._fetch_kenyan("12345678")
        r.check("an EXPIRED entry is not used", why != "stale yes",
                f"got {why!r} - a stale verdict must be re-fetched")
    finally:
        if saved_key2 is not None:
            os.environ["COMPANIES_HOUSE_API_KEY"] = saved_key2
    api_v2._nat_cache.clear()


def check_filter(r: Results) -> None:
    print("\n-- the filter: what it keeps, hides and reports --")
    verdicts = {
        "00000001": (True,  "2 of 2 active directors Kenyan"),
        "00000002": (False, "no Kenyan director among 3 active"),
        "00000003": (False, "1 of 2 active directors have no nationality recorded"),
    }
    saved = api_v2._fetch_kenyan
    api_v2._fetch_kenyan = lambda cn: verdicts.get(cn, (False, "unknown"))
    saved_rem = api_v2._ch_remain[0]
    try:
        api_v2._ch_remain[0] = 600
        cands = [{"cn": c, "name": f"CO {c}"} for c in sorted(verdicts)]
        kept, excluded, note = api_v2._apply_kenyan_filter(cands)
        r.check("only the admissible candidate is kept",
                [k["cn"] for k in kept] == ["00000001"],
                f"kept {[k['cn'] for k in kept]}")
        r.check("the rest are counted as hidden", excluded == 2, f"got {excluded}")
        r.check("the note says how many were hidden",
                "2 hidden" in note, f"note={note!r}")
        r.check("every candidate carries its reason",
                all("kenyan_reason" in c for c in cands),
                "an operator must be able to see WHY one was hidden")

        # Nothing to do must not invent a note.
        kept, excluded, note = api_v2._apply_kenyan_filter([])
        r.check("no candidates gives no note",
                (kept, excluded, note) == ([], 0, ""), f"got {(kept, excluded, note)!r}")

        # The per-search ceiling, which is what stops one search eating the
        # whole 5-minute budget.
        many = [{"cn": "00000001", "name": "x"} for _ in range(30)]
        kept, excluded, note = api_v2._apply_kenyan_filter(many)
        r.check("no more than the ceiling is examined",
                len(kept) == api_v2._NAT_MAX_PER_SEARCH,
                f"examined {len(kept)}, ceiling {api_v2._NAT_MAX_PER_SEARCH}")
        r.check("...and the unchecked remainder is reported",
                "not checked" in note, f"note={note!r}")

        # A low quota must be visible, not silent.
        api_v2._ch_remain[0] = 10
        kept, excluded, note = api_v2._apply_kenyan_filter(
            [{"cn": "00000001", "name": "x"}])
        r.check("a low Companies House quota is reported",
                "quota low" in note, f"note={note!r}")
        api_v2._ch_remain[0] = 600
        kept, excluded, note = api_v2._apply_kenyan_filter(
            [{"cn": "00000001", "name": "x"}])
        r.check("...and is not reported when the quota is healthy",
                "quota low" not in note, f"note={note!r}")
    finally:
        api_v2._fetch_kenyan = saved
        api_v2._ch_remain[0] = saved_rem


def check_route(r: Results) -> None:
    print("\n-- the search route: local rows are never filtered --")
    # A company already in the pipeline must stay findable. Filtering those
    # would leave an operator unable to find their own companies, which is a
    # worse bug than showing one that no longer qualifies.
    saved_rows, saved_fetch = api_v2._all_rows, api_v2._fetch_kenyan
    api_v2._fetch_kenyan = lambda cn: (False, "would be refused")
    api_v2._all_rows = lambda: [{"Company Number": "16940106",
                                 "Company Name": "ACME ADVISORY CONSULTING",
                                 "Directors": "", "Date of Creation": "2024-01-01"}]
    try:
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(api_v2.api_v2)
        with app.test_client() as c:
            body = c.get("/api/v2/company/search?q=acme").get_json()
        cns = [x["cn"] for x in body.get("results", [])]
        r.check("a company already in the pipeline is still returned",
                "16940106" in cns,
                f"got {cns} - the filter must not hide the operator's own rows")
        r.check("...and no filter note is attached to a local-only result",
                "kenyan_note" not in body, f"body keys {sorted(body)}")
    finally:
        api_v2._all_rows, api_v2._fetch_kenyan = saved_rows, saved_fetch


def main() -> int:
    r = Results()
    print(f"target: the Kenyan-director admission rule in {REPO / 'api_v2.py'}")
    check_parser(r)
    check_rule(r)
    check_fetch(r)
    check_filter(r)
    check_route(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
