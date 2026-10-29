#!/usr/bin/env python3
"""A company row must fill itself from Companies House when it is added.

    python3 test_autofill.py

Hermetic: no network, no database. Every officer and profile record below
was captured from a LIVE Companies House response on 2026-10-29, so the
shapes are the real ones rather than shapes that would be convenient.

What this guards:

  * api_v2's details stage used to spell out its own header -> value map
    and four of its ten keys did not exist on the dict it read from:
    "type", "date_of_incorporation", "director_dobs", "director_genders".
    db._norm("") is None and db.update_row has no blank-skip, so each run
    of that stage wrote NULL into Type, Director DOB and Director Gender
    and ERASED the Date of Creation the add route had just stored. The
    mapping now lives in one function and this file proves the columns
    arrive filled.

  * The four director columns describe the Kenyan director alone, and WHICH
    Kenyan director must not depend on the order Companies House returned.
    FC010541 is the case that matters: eleven active Kenyan directors, all
    appointed 1980-07-11.

  * Developer Email is dev@ the company's own domain, derived once in
    db.update_row rather than at the four places that write a domain.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

import db                           # noqa: E402
import directors                    # noqa: E402
import api_v2                       # noqa: E402
import run_pipeline                 # noqa: E402
import play_console_readiness as pcr  # noqa: E402


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


# ── live captures ──────────────────────────────────────────────────────────
# KENYA AIRWAYS PLC. Three Kenyan directors among five, an oversea company
# so the profile carries NO sic_codes key at all, and an address with a
# region but no postal_code.
FC009164_PROFILE = {
    "company_name": "KENYA AIRWAYS PLC",
    "company_status": "active",
    "type": "oversea-company",
    "date_of_creation": "1977-04-06",
    "registered_office_address": {
        "address_line_1": "Kenya Airways Headquarters And Base Airport North Road",
        "address_line_2": "Embakasi",
        "locality": "PO BOX 19002, 00501",
        "region": "Nairobi",
        "country": "Kenya",
    },
}
FC009164_OFFICERS = [
    {"name": "BUCKLEY, Christopher James", "nationality": "Canadian",
     "officer_role": "director", "appointed_on": "2023-03-27",
     "date_of_birth": {"month": 8, "year": 1958}},
    {"name": "DAGHAR, Mohamed Abdulkarim", "nationality": "Kenyan",
     "officer_role": "director", "appointed_on": "2022-12-22",
     "date_of_birth": {"month": 3, "year": 1986}},
    {"name": "MBUVI, Alex Musyoka", "nationality": "Kenyan",
     "officer_role": "director", "appointed_on": "2023-11-28",
     "date_of_birth": {"month": 6, "year": 1975}},
    {"name": "WAMBUGU, Phillip", "nationality": "Kenyan",
     "officer_role": "director", "appointed_on": "2021-06-25",
     "date_of_birth": {"month": 2, "year": 1960}},
    {"name": "WILSON, John", "nationality": "Swedish",
     "officer_role": "director", "appointed_on": "2021-06-25",
     "date_of_birth": {"month": 3, "year": 1961}},
    # A resigned Kenyan director, and a Kenyan secretary. Neither may be
    # chosen: the first is no longer a director, the second never was.
    {"name": "RESIGNED, Kenyan One", "nationality": "Kenyan",
     "officer_role": "director", "appointed_on": "1990-01-01",
     "resigned_on": "1999-01-01",
     "date_of_birth": {"month": 1, "year": 1950}},
    {"name": "SECRETARY, Kenyan Two", "nationality": "Kenyan",
     "officer_role": "secretary", "appointed_on": "1990-01-01",
     "date_of_birth": {"month": 1, "year": 1951}},
]

# Eleven Kenyan directors, ALL appointed 1980-07-11, and not one has a
# published date of birth. The tie is the point.
FC010541_OFFICERS = [
    {"name": n, "nationality": "Kenyan", "officer_role": "director",
     "appointed_on": "1980-07-11", "date_of_birth": None}
    for n in ["BOSWELL, Graham Leonard William",
              "COLE, Andrew John Galbraith Viscount",
              "DANGANA, Maurice", "GICHUKI, Gerishon Waruiru",
              "GITAU, Joseph Muriuki", "JANI, Prahlad Kalyanji",
              "KIPSANAI, Joseph Anthony Kandie", "MATHU, Eliud Wambu",
              "MBUGUA, Simon", "NGANGA, Nicholas", "NYAGA, Richard Stanley"]
] + [{"name": "TOMPKINS, Roger Manning", "nationality": "New Zealander",
      "officer_role": "director", "appointed_on": "1980-07-11",
      "date_of_birth": None}]

# A dual national - 'British,Kenyan' - which is the whole reason the
# nationality split is on commas.
SC461938_PROFILE = {
    "company_name": "KWISA - WOMEN OF AFRICAN HERITAGE CIC",
    "company_status": "active",
    "type": "private-limited-guarant-nsc",
    "date_of_creation": "2013-10-21",
    "sic_codes": ["94990"],
    "registered_office_address": {
        "address_line_1": "Room 4 Greyfriars Charteris Centre",
        "address_line_2": "138-140 The Pleasance",
        "locality": "Edinburgh", "postal_code": "EH8 9RR",
        "country": "Scotland",
    },
}
SC461938_OFFICERS = [
    {"name": "DUBE, Naphtali Musawenkosi", "nationality": "British",
     "officer_role": "director", "appointed_on": "2026-08-03",
     "date_of_birth": {"month": 5, "year": 2001}},
    {"name": "KAMONJI, Esther Wanjiru", "nationality": "British",
     "officer_role": "director", "appointed_on": "2017-10-21",
     "date_of_birth": {"month": 12, "year": 1959}},
    {"name": "KIMANI, Elisha Kang'Ethe", "nationality": "British,Kenyan",
     "officer_role": "director", "appointed_on": "2021-06-07",
     "date_of_birth": {"month": 4, "year": 1965}},
    {"name": "VIZA, Evdoxia, Dr.", "nationality": "Greek",
     "officer_role": "director", "appointed_on": "2020-01-25",
     "date_of_birth": {"month": 3, "year": 1975}},
]

# British only. Not admissible at all, and the one officer HAS a published
# date of birth - so it is the control for "the row stays empty because the
# company was refused", not "because Companies House said nothing".
C08227886_PROFILE = {
    "company_name": "ACME ADVISORY LTD",
    "company_status": "active", "type": "ltd",
    "date_of_creation": "2012-09-25",
    "sic_codes": ["69201", "70221", "70229"],
    "registered_office_address": {
        "address_line_1": "7 Bell Yard", "locality": "London",
        "postal_code": "WC2A 2JR", "country": "England"},
}
C08227886_OFFICERS = [
    {"name": "KIRKBRIDE, Jason", "nationality": "British",
     "officer_role": "director", "appointed_on": "2012-09-25",
     "date_of_birth": {"month": 1, "year": 1984}},
]


# ── the selection rule ─────────────────────────────────────────────────────

def check_selection(r: Results) -> None:
    print("\n-- which director the row describes --")
    pick = directors.kenyan_directors

    got = pick(FC009164_OFFICERS)
    r.check("only Kenyan directors are candidates",
            [o["name"] for o in got] == ["WAMBUGU, Phillip",
                                         "DAGHAR, Mohamed Abdulkarim",
                                         "MBUVI, Alex Musyoka"],
            f"got {[o['name'] for o in got]}")
    r.check("...the earliest appointment first",
            got[0]["name"] == "WAMBUGU, Phillip", got[0]["name"])
    r.check("a RESIGNED Kenyan director is never chosen",
            all("RESIGNED" not in o["name"] for o in got),
            f"got {[o['name'] for o in got]}")
    r.check("a Kenyan SECRETARY is never chosen",
            all("SECRETARY" not in o["name"] for o in got),
            f"got {[o['name'] for o in got]}")

    # The tie. Eleven candidates, one date. Without the name tiebreak the
    # answer is whatever order the API used, so feeding the SAME officers
    # in a different order must still name the same person.
    first = directors.kenyan_directors(FC010541_OFFICERS)[0]["name"]
    reversed_ = directors.kenyan_directors(
        list(reversed(FC010541_OFFICERS)))[0]["name"]
    shuffled = directors.kenyan_directors(
        FC010541_OFFICERS[5:] + FC010541_OFFICERS[:5])[0]["name"]
    r.check("eleven directors appointed the same day: eleven candidates",
            len(directors.kenyan_directors(FC010541_OFFICERS)) == 11,
            f"got {len(directors.kenyan_directors(FC010541_OFFICERS))}")
    r.check("...and the choice does not depend on the API's order",
            first == reversed_ == shuffled,
            f"as given={first!r} reversed={reversed_!r} rotated={shuffled!r}")
    r.check("...it is the alphabetically first of the tied directors",
            first == "BOSWELL, Graham Leonard William", first)
    r.check("the New Zealander is not a candidate",
            all("TOMPKINS" not in o["name"]
                for o in directors.kenyan_directors(FC010541_OFFICERS)))

    dual = directors.kenyan_directors(SC461938_OFFICERS)
    r.check("a dual national 'British,Kenyan' counts as Kenyan",
            [o["name"] for o in dual] == ["KIMANI, Elisha Kang'Ethe"],
            f"got {[o['name'] for o in dual]}")
    r.check("a British-only board has no Kenyan director",
            directors.kenyan_directors(C08227886_OFFICERS) == [])

    # 'South African' must not be split into 'south' and 'african'.
    r.check("a nationality containing a space stays one value",
            directors.nationalities({"nationality": "South African"})
            == ["south african"])
    r.check("a missing nationality is no nationality, not a blank one",
            directors.nationalities({"nationality": None}) == [])
    r.check("...and such a director is not Kenyan",
            directors.kenyan_directors(
                [{"name": "X", "nationality": None,
                  "officer_role": "director"}]) == [])

    r.check("a date of birth becomes YYYY-MM",
            directors.dob_month(SC461938_OFFICERS[2]) == "1965-04",
            directors.dob_month(SC461938_OFFICERS[2]))
    r.check("...zero-padded on a single-digit month",
            directors.dob_month(FC009164_OFFICERS[3]) == "1960-02",
            directors.dob_month(FC009164_OFFICERS[3]))
    r.check("an unpublished date of birth is empty, not a guess",
            directors.dob_month(FC010541_OFFICERS[0]) == "",
            directors.dob_month(FC010541_OFFICERS[0]))
    r.check("...and a year with no month is too",
            directors.dob_month({"date_of_birth": {"year": 1984}}) == "")

    # pick_kenyan must work on details["directors"], which has already been
    # reduced to active directors and carries NO officer_role. Running the
    # role filter over it would discard every entry and name nobody.
    already = [{"name": o["name"], "nationality": o["nationality"],
                "appointed_on": o["appointed_on"]} for o in FC009164_OFFICERS
               if not o.get("resigned_on") and o["officer_role"] == "director"]
    r.check("pick_kenyan works on a list with no officer_role",
            [o["name"] for o in directors.pick_kenyan(already)][:1]
            == ["WAMBUGU, Phillip"],
            f"got {[o['name'] for o in directors.pick_kenyan(already)]}")
    r.check("...while kenyan_directors would correctly reject it",
            directors.kenyan_directors(already) == [],
            "kenyan_directors requires officer_role; that is the difference")


# ── the row mapping ────────────────────────────────────────────────────────

def check_row_updates(r: Results) -> None:
    print("\n-- the columns Companies House fills --")
    u = run_pipeline.company_row_updates(FC009164_PROFILE, FC009164_OFFICERS)

    r.check("Company Name", u.get("Company Name") == "KENYA AIRWAYS PLC",
            repr(u.get("Company Name")))
    r.check("Status", u.get("Status") == "active", repr(u.get("Status")))
    # The two keys the old call site asked for under the wrong names, so
    # these two are the regression that was live.
    r.check("Type is filled, not blanked",
            u.get("Type") == "oversea-company", repr(u.get("Type")))
    r.check("Date of Creation is filled, not erased",
            u.get("Date of Creation") == "1977-04-06",
            repr(u.get("Date of Creation")))
    r.check("Address joins the parts that are present",
            u.get("Address") == ("Kenya Airways Headquarters And Base Airport "
                                 "North Road, Embakasi, PO BOX 19002, 00501, "
                                 "Nairobi, Kenya"),
            repr(u.get("Address")))
    r.check("...with no empty slot for the absent postal_code",
            ", ," not in (u.get("Address") or ""), repr(u.get("Address")))

    # Not an observed shape: across the four live profiles captured here,
    # Companies House omits a key it has nothing for rather than sending
    # null. These two are constructed, and they are the reason the filter
    # is on the value - on the key, a null becomes the literal "None" in
    # the middle of an address and an empty string becomes a ", ," gap.
    r.check("a null address part is dropped, not rendered as 'None'",
            run_pipeline.format_address(
                {"address_line_1": "1 High St", "address_line_2": None,
                 "locality": "London"}) == "1 High St, London",
            repr(run_pipeline.format_address(
                {"address_line_1": "1 High St", "address_line_2": None,
                 "locality": "London"})))
    r.check("...and so is an empty one",
            run_pipeline.format_address(
                {"address_line_1": "1 High St", "locality": "",
                 "postal_code": "W1"}) == "1 High St, W1",
            repr(run_pipeline.format_address(
                {"address_line_1": "1 High St", "locality": "",
                 "postal_code": "W1"})))
    r.check("no address at all is an empty string, not a crash",
            run_pipeline.format_address(None) == ""
            and run_pipeline.format_address({}) == "")

    r.check("Directors is the Kenyan director alone",
            u.get("Directors") == "WAMBUGU, Phillip", repr(u.get("Directors")))
    r.check("Director Nationalities matches that director",
            u.get("Director Nationalities") == "Kenyan",
            repr(u.get("Director Nationalities")))
    r.check("Director DOB is filled - it never was before",
            u.get("Director DOB") == "1960-02", repr(u.get("Director DOB")))
    r.check("Director Gender is filled - it never was before",
            u.get("Director Gender") == "M", repr(u.get("Director Gender")))
    r.check("...and it is what _infer_gender says for that name",
            u.get("Director Gender")
            == run_pipeline._infer_gender("WAMBUGU, Phillip"),
            f"{u.get('Director Gender')!r} vs "
            f"{run_pipeline._infer_gender('WAMBUGU, Phillip')!r}")

    # The two names from these live records that _infer_gender could not
    # resolve, which is why the gender column kept coming out empty.
    for name, want in (("WAMBUGU, Phillip", "M"),
                       ("DAGHAR, Mohamed Abdulkarim", "M"),
                       ("KIRKBRIDE, Jason", "M"),
                       ("KAMONJI, Esther Wanjiru", "F")):
        r.check(f"_infer_gender resolves {name!r}",
                run_pipeline._infer_gender(name) == want,
                f"got {run_pipeline._infer_gender(name)!r}, want {want!r}")
    r.check("...and still declines a name it does not know",
            run_pipeline._infer_gender("VIZA, Evdoxia") == "",
            f"got {run_pipeline._infer_gender('VIZA, Evdoxia')!r} - "
            f"a guess here lands on an identity document")

    # Invariants on the dictionaries themselves. A name in both sets would
    # make the answer depend on which loop ran first.
    both = run_pipeline._MALE_NAMES & run_pipeline._FEMALE_NAMES
    r.check("no name is in both the male and the female set", not both,
            f"in both: {sorted(both)}")
    unisex_m = run_pipeline._UNISEX_NAMES & run_pipeline._MALE_NAMES
    unisex_f = run_pipeline._UNISEX_NAMES & run_pipeline._FEMALE_NAMES
    r.check("no unisex name is also claimed as male or female",
            not unisex_m and not unisex_f,
            f"male: {sorted(unisex_m)} female: {sorted(unisex_f)}")
    r.check("every name is lowercase, which is how they are looked up",
            all(n == n.lower() for n in
                run_pipeline._MALE_NAMES | run_pipeline._FEMALE_NAMES
                | run_pipeline._UNISEX_NAMES),
            "the lookup lowercases the token, so an uppercase entry is dead")

    # sic_codes is ABSENT from an oversea-company profile, not empty.
    r.check("an absent sic_codes writes no SIC Codes key",
            "SIC Codes" not in u, f"got {u.get('SIC Codes')!r}")
    u2 = run_pipeline.company_row_updates(SC461938_PROFILE, SC461938_OFFICERS)
    r.check("...and a present one is joined",
            u2.get("SIC Codes") == "94990", repr(u2.get("SIC Codes")))
    u3 = run_pipeline.company_row_updates(C08227886_PROFILE, C08227886_OFFICERS)
    r.check("...several are comma-joined",
            u3.get("SIC Codes") == "69201, 70221, 70229",
            repr(u3.get("SIC Codes")))

    # This is the invariant that protects a hand-corrected cell.
    r.check("NO key is ever present with an empty value",
            all(v for v in u.values()), {k: v for k, v in u.items() if not v})
    empty = run_pipeline.company_row_updates({}, [])
    r.check("an empty profile produces an empty update, not six blanks",
            empty == {}, f"got {empty!r}")
    r.check("...so db.update_row would write nothing at all", not empty)

    # A company with no published DOB must omit the column rather than
    # blank it - pipeline.stage_director_id back-fills DOB from CH.
    u4 = run_pipeline.company_row_updates(SC461938_PROFILE, FC010541_OFFICERS)
    r.check("a director with no published DOB omits Director DOB",
            "Director DOB" not in u4, f"got {u4.get('Director DOB')!r}")
    r.check("...but still records the director",
            u4.get("Directors") == "BOSWELL, Graham Leonard William",
            repr(u4.get("Directors")))

    # No Kenyan director: the company columns still fill, the director
    # columns stay untouched. The admission guard is what refuses the
    # company; this function does not double as the gate.
    r.check("a non-Kenyan board leaves all four director columns absent",
            not any(k.startswith("Director") or k == "Directors" for k in u3),
            f"got {sorted(u3)}")
    r.check("...while the company columns are still filled",
            u3.get("Company Name") == "ACME ADVISORY LTD"
            and u3.get("Type") == "ltd", f"got {u3!r}")


def check_mapping_is_shared(r: Results) -> None:
    print("\n-- one mapping, used by every writer --")
    src = (REPO / "api_v2.py").read_text(encoding="utf-8")
    r.check("the details stage writes row_updates, not its own dict",
            'd["row_updates"]' in src,
            "a second copy of the mapping is how the four wrong keys "
            "survived unnoticed")
    # Comments are stripped first. The comment at that call site NAMES the
    # four wrong keys so the next reader knows what went wrong, and a
    # search over the raw text would be satisfied by that comment - a
    # check that passes on prose is not a check on the code.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    for dead in ('"date_of_incorporation"', '"director_dobs"',
                 '"director_genders"', 'd.get("type"'):
        r.check(f"the non-existent key {dead} is gone from api_v2's code",
                dead not in code)
    r.check("...and the stripping is what makes that bite",
            '"director_dobs"' in src,
            "the comment should still name them; if it does not, this "
            "check could pass for the wrong reason")

    # save_results must not put every active director back over the one the
    # add chose. It maps a nested result dict, so it has to prefer
    # row_updates where the result carries it.
    result = {
        "company_number": "FC009164",
        "details": {
            # The flat keys, as get_company_details still returns them:
            # every active director, and no DOB or gender at all.
            "company_name": "KENYA AIRWAYS PLC",
            "company_type": "oversea-company",
            "director_names": "BUCKLEY, Christopher James; "
                              "DAGHAR, Mohamed Abdulkarim; WAMBUGU, Phillip",
            "director_nationalities": "Canadian; Kenyan; Kenyan",
            "row_updates": run_pipeline.company_row_updates(
                FC009164_PROFILE, FC009164_OFFICERS),
        },
    }
    headers = db._result_to_headers(result)
    r.check("save_results writes the Kenyan director, not all three",
            headers.get("Directors") == "WAMBUGU, Phillip",
            repr(headers.get("Directors")))
    r.check("...and carries the DOB the flat keys never had",
            headers.get("Director DOB") == "1960-02",
            repr(headers.get("Director DOB")))
    r.check("...and the Type the flat key spelled differently",
            headers.get("Type") == "oversea-company",
            repr(headers.get("Type")))

    # A result rebuilt from a row has no row_updates; the flat keys stand.
    legacy = {"company_number": "FC009164",
              "details": {"company_name": "X LTD",
                          "director_names": "A; B"}}
    r.check("a result with no row_updates still maps the flat keys",
            db._result_to_headers(legacy).get("Directors") == "A; B",
            repr(db._result_to_headers(legacy).get("Directors")))


# ── the add route ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, code: int, body: dict | None = None):
        self.status_code = code
        self._body = body or {}
        self.headers = {"X-Ratelimit-Remain": "500"}

    def json(self):
        return self._body


def check_autofill(r: Results) -> None:
    print("\n-- _autofill_company --")
    saved_get = api_v2._ch_session.get
    saved_update = db.update_row
    writes: list[tuple[str, dict]] = []
    db.update_row = lambda cn, upd: writes.append((cn, dict(upd)))
    os.environ["COMPANIES_HOUSE_API_KEY"] = "test-key"
    try:
        # The officer list comes from the cache the admission check filled,
        # so the profile is the ONLY request the autofill makes.
        calls: list[str] = []

        def _get(url, **kw):
            calls.append(url)
            return _Resp(200, FC009164_PROFILE)

        api_v2._ch_session.get = _get
        api_v2._off_cache.clear()
        api_v2._off_cache["FC009164"] = (time.time() + 3600, FC009164_OFFICERS)

        filled, err = api_v2._autofill_company("FC009164")
        r.check("the autofill reports no error", err == "", err)
        r.check("...making exactly ONE request", len(calls) == 1, f"{calls}")
        r.check("...for the company profile",
                calls and calls[0].endswith("/company/FC009164"),
                f"{calls}")
        r.check("...and one write", len(writes) == 1, f"{writes}")
        # Nine, not ten: FC009164 is an oversea company, so its profile
        # has no sic_codes key and SIC Codes is correctly left out.
        r.check("...of nine columns",
                sorted(writes[0][1]) == [
                    "Address", "Company Name", "Date of Creation",
                    "Director DOB", "Director Gender",
                    "Director Nationalities", "Directors", "Status", "Type"],
                f"wrote {sorted(writes[0][1])}")
        r.check("...including the Kenyan director",
                writes[0][1].get("Directors") == "WAMBUGU, Phillip",
                repr(writes[0][1].get("Directors")))
        r.check("...and what it says it filled is what it wrote",
                filled == writes[0][1], f"{filled!r} vs {writes[0][1]!r}")

        # Failure must be reported and must not write.
        for code in (404, 429, 500):
            writes.clear()
            api_v2._ch_session.get = lambda *a, **k: _Resp(code)
            filled, err = api_v2._autofill_company("FC009164")
            r.check(f"HTTP {code} writes nothing", writes == [], f"{writes}")
            r.check(f"...and says so", str(code) in err, err)

        writes.clear()
        api_v2._ch_session.get = lambda *a, **k: (_ for _ in ()).throw(
            OSError("boom"))
        filled, err = api_v2._autofill_company("FC009164")
        r.check("a network failure writes nothing", writes == [], f"{writes}")
        r.check("...and names the failure", "OSError" in err, err)

        writes.clear()
        api_v2._ch_session.get = _get
        api_v2._off_cache.clear()
        filled, err = api_v2._autofill_company("FC009164")
        r.check("with no cached officers it writes nothing",
                writes == [] and filled == {}, f"{writes} {filled}")
        r.check("...rather than a row with no director",
                "officer list" in err, err)

        # An expired cache entry is not a cache hit.
        api_v2._off_cache["FC009164"] = (time.time() - 1, FC009164_OFFICERS)
        filled, err = api_v2._autofill_company("FC009164")
        r.check("a STALE officer entry is not used",
                filled == {} and "officer list" in err, f"{filled} {err}")

        # A write failure must not take the add down with it.
        api_v2._off_cache["FC009164"] = (time.time() + 3600, FC009164_OFFICERS)
        db.update_row = lambda cn, upd: (_ for _ in ()).throw(
            RuntimeError("db down"))
        filled, err = api_v2._autofill_company("FC009164")
        r.check("a database failure is reported, not raised",
                filled == {} and "db down" in err, f"{filled} {err}")

        os.environ.pop("COMPANIES_HOUSE_API_KEY")
        filled, err = api_v2._autofill_company("FC009164")
        r.check("no API key fills nothing and says which key",
                filled == {} and "COMPANIES_HOUSE_API_KEY" in err, err)
    finally:
        api_v2._ch_session.get = saved_get
        db.update_row = saved_update
        api_v2._off_cache.clear()
        os.environ["COMPANIES_HOUSE_API_KEY"] = "test-key"


def check_officer_cache(r: Results) -> None:
    print("\n-- the admission check pays for the officer list once --")
    saved_get = api_v2._ch_session.get
    os.environ["COMPANIES_HOUSE_API_KEY"] = "test-key"
    try:
        api_v2._nat_cache.clear()
        api_v2._off_cache.clear()
        api_v2._ch_session.get = lambda *a, **k: _Resp(
            200, {"items": FC009164_OFFICERS})
        ok, why = api_v2._fetch_kenyan("FC009164")
        r.check("the company is admitted", ok, why)
        r.check("...and its officer list is cached for the autofill",
                "FC009164" in api_v2._off_cache,
                f"cache holds {sorted(api_v2._off_cache)}")
        r.check("...as the payload itself",
                api_v2._off_cache["FC009164"][1] == FC009164_OFFICERS)
        r.check("the verdict cache keeps its 3-tuple shape",
                len(api_v2._nat_cache["FC009164"]) == 3,
                f"got {api_v2._nat_cache['FC009164']!r}")

        # A failed lookup must cache neither, or a company that is in fact
        # eligible would keep being refused.
        api_v2._nat_cache.clear()
        api_v2._off_cache.clear()
        api_v2._ch_session.get = lambda *a, **k: _Resp(429)
        ok, why = api_v2._fetch_kenyan("00000009")
        r.check("a 429 caches no verdict", "00000009" not in api_v2._nat_cache)
        r.check("...and no officer list", "00000009" not in api_v2._off_cache)
    finally:
        api_v2._ch_session.get = saved_get
        api_v2._nat_cache.clear()
        api_v2._off_cache.clear()


# ── Developer Email ────────────────────────────────────────────────────────

def check_developer_email(r: Results) -> None:
    print("\n-- Developer Email is dev@ the company's own domain --")
    sent: list[tuple[str, list]] = []

    class _Cur:
        def execute(self, sql, params=None):
            sent.append((sql, list(params or [])))

        def fetchone(self):
            return None

    import contextlib

    @contextlib.contextmanager
    def _fake_cursor():
        yield _Cur()

    saved_cursor, saved_inval = db._cursor, db._invalidate
    db._cursor, db._invalidate = _fake_cursor, lambda: None
    try:
        def written(updates: dict) -> dict:
            sent.clear()
            db.update_row("00000001", updates)
            if not sent:
                return {}
            sql, params = sent[-1]
            cols = sql.split("(", 1)[1].split(")", 1)[0].replace(" ", "").split(",")
            # params[0] is company_number, which heads the column list.
            return dict(zip(cols, params))

        got = written({"Domain": "acmeadvisory.uk"})
        r.check("writing a Domain derives the Developer Email",
                got.get("developer_email") == "dev@acmeadvisory.uk",
                f"got {got!r}")
        got = written({"Domain": "AcmeAdvisory.UK"})
        r.check("...lowercased, because an address is not case-sensitive",
                got.get("developer_email") == "dev@acmeadvisory.uk",
                f"got {got.get('developer_email')!r}")
        got = written({"Domain": "x.uk", "Developer Email": "chosen@x.uk"})
        r.check("a Developer Email the caller supplied wins",
                got.get("developer_email") == "chosen@x.uk",
                f"got {got.get('developer_email')!r}")
        got = written({"Domain": "x.uk", "Developer Email": ""})
        r.check("...but a BLANK one does not, or the column stays NULL",
                got.get("developer_email") == "dev@x.uk",
                f"got {got.get('developer_email')!r}")
        got = written({"Company Name": "ACME LTD"})
        r.check("a write with no Domain derives nothing",
                "developer_email" not in got, f"got {got!r}")
        got = written({"Domain": ""})
        r.check("a blank Domain derives nothing",
                "developer_email" not in got, f"got {got!r}")
        r.check("...and such a write still reaches the database",
                sent != [], "a blank domain is still an intentional clear")

        # The same formula the Play Console code uses, not a second one.
        r.check("it agrees with developer_email_for_domain",
                pcr.developer_email_for_domain("acmeadvisory.uk")
                == "dev@acmeadvisory.uk",
                pcr.developer_email_for_domain("acmeadvisory.uk"))
    finally:
        db._cursor, db._invalidate = saved_cursor, saved_inval

    # The Excel import writes through its own SQL, not update_row, so the
    # derivation has to be in the parse the import and its diff preview
    # share - otherwise an import would silently fill a field the operator
    # never saw in the preview, or not fill it at all.
    import openpyxl
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "sheet.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Company Number", "Company Name", "Domain", "Developer Email"])
    ws.append(["00000001", "ACME LTD", "acmeadvisory.uk", ""])
    ws.append(["00000002", "BETA LTD", "beta.uk", "chosen@beta.uk"])
    ws.append(["00000003", "GAMMA LTD", "", ""])
    wb.save(tmp)
    recs = dict(db._excel_records(str(tmp)))
    r.check("an imported domain derives the developer address",
            recs["00000001"].get("developer_email") == "dev@acmeadvisory.uk",
            f"got {recs['00000001']!r}")
    r.check("...a Developer Email column in the sheet wins",
            recs["00000002"].get("developer_email") == "chosen@beta.uk",
            f"got {recs['00000002']!r}")
    r.check("...and a row with no domain derives nothing",
            "developer_email" not in recs["00000003"],
            f"got {recs['00000003']!r}")
    # The preview must read the SAME parse as the import, or it would
    # preview a write that is not the write that happens.
    import inspect
    for fn in (db.diff_from_excel, db.import_from_excel):
        r.check(f"{fn.__name__} reads _excel_records",
                "_excel_records(" in inspect.getsource(fn),
                "a second parse here would drift from the other")


# ── the Play Console dossier ───────────────────────────────────────────────

def check_dossier_director(r: Results) -> None:
    print("\n-- the dossier names the Kenyan director --")
    # details["directors"]: already active-only, and with NO officer_role.
    actives = [{"name": o["name"], "nationality": o["nationality"],
                "appointed_on": o["appointed_on"]}
               for o in FC009164_OFFICERS
               if not o.get("resigned_on") and o["officer_role"] == "director"]
    d = pcr._first_director({"directors": actives})
    r.check("not simply the first officer listed",
            d["name"] != "BUCKLEY, Christopher James", d["name"])
    r.check("...but the earliest-appointed Kenyan one",
            d["name"] == "WAMBUGU, Phillip", d["name"])
    r.check("...with that director's nationality",
            d["nationality"] == "Kenyan", d["nationality"])
    r.check("...and the name parsed out of 'SURNAME, Forename'",
            (d["first_name"], d["last_name"]) == ("Phillip", "Wambugu"),
            f"{d['first_name']!r} {d['last_name']!r}")

    # No Kenyan director at all: name someone rather than nobody.
    only_british = [{"name": "KIRKBRIDE, Jason", "nationality": "British"}]
    d = pcr._first_director({"directors": only_british})
    r.check("with no Kenyan director it falls back to the first",
            d["name"] == "KIRKBRIDE, Jason", d["name"])

    # The ';'-joined fallback, as a legacy row holding every director.
    d = pcr._first_director({
        "director_names": "BUCKLEY, Christopher James; WAMBUGU, Phillip",
        "director_nationalities": "Canadian; Kenyan"})
    r.check("a legacy row's lists are walked for the Kenyan one",
            d["name"] == "WAMBUGU, Phillip", d["name"])
    r.check("...with the matching nationality",
            d["nationality"] == "Kenyan", d["nationality"])
    d = pcr._first_director({
        "director_names": "WAMBUGU, Phillip",
        "director_nationalities": "Kenyan"})
    r.check("a Kenyan-only row needs no walking",
            d["name"] == "WAMBUGU, Phillip", d["name"])
    d = pcr._first_director({"director_names": "A, One; B, Two",
                             "director_nationalities": "British; Greek"})
    r.check("...and with no Kenyan in the lists, the first is used",
            d["name"] == "A, One", d["name"])
    d = pcr._first_director({})
    r.check("no directors at all names nobody, and does not raise",
            d["name"] == "" and d["display_name"] == "", f"{d!r}")


def main() -> int:
    r = Results()
    print("target: filling a company row from Companies House at add time")
    check_selection(r)
    check_row_updates(r)
    check_mapping_is_shared(r)
    check_autofill(r)
    check_officer_cache(r)
    check_developer_email(r)
    check_dossier_director(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
