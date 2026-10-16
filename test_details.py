"""
The details subpipeline — the Companies House lookup every other stage reads.

Three defects, all of which lost or invented information silently:

  1. The stage wrote "Director DOB" and "Director Gender" from keys
     get_company_details never returned, so every run replaced both with an
     empty string. Anything a person had entered by hand was gone.
  2. Gender is not in the Companies House officers resource at all, so that
     field could never have been populated by this stage — it could only
     ever blank it.
  3. Dates of birth *are* published (month and year) and were never read.
  4. Officers come back paginated, and resigned officers share the list with
     serving ones. One unpaged request meant a company with a long history
     could report no directors because the first page was all leavers.

Separately, the dashboard called the stage Done whenever a name was present,
so a manually added company showed "Company Details: Done" with an empty
address, no SIC codes and no directors.

Companies House is stubbed; no request leaves the machine.

    python test_details.py
"""

from __future__ import annotations

import run_pipeline


def _officer(name, *, role="director", resigned=None, year=None, month=None,
             nationality="British"):
    item = {"name": name, "officer_role": role, "nationality": nationality,
            "appointed_on": "2020-01-01"}
    if resigned:
        item["resigned_on"] = resigned
    if year:
        item["date_of_birth"] = {"year": year, **({"month": month} if month else {})}
    return item


PROFILE = {
    "company_name": "NORTHGATE CARE LTD",
    "company_status": "active",
    "type": "ltd",
    "date_of_creation": "2019-04-02",
    "sic_codes": ["88100"],
    "registered_office_address": {
        "address_line_1": "12 Dock Road", "locality": "Liverpool",
        "postal_code": "L3 4AA", "country": "England",
    },
}


def _stub(officer_pages):
    """officer_pages: list of (items, total_results) served in request order."""
    calls = []

    def ch_get(path, params=None):
        calls.append((path, dict(params or {})))
        if path.endswith("/officers"):
            index = len([c for c in calls if c[0].endswith("/officers")]) - 1
            items, total = officer_pages[min(index, len(officer_pages) - 1)]
            return {"items": items, "total_results": total}
        return PROFILE

    run_pipeline.ch_get = ch_get
    return calls


def test_dobs_are_read_from_companies_house() -> None:
    _stub([([_officer("OKAFOR, Jane", year=1985, month=3),
             _officer("SMITH, Alan", year=1972, month=11)], 2)])
    d = run_pipeline.get_company_details("00000001")
    assert d["director_dobs"] == "1985-03; 1972-11", d["director_dobs"]
    assert d["director_names"] == "OKAFOR, Jane; SMITH, Alan", d["director_names"]
    assert d["director_nationalities"] == "British; British", d["director_nationalities"]
    print("  OK  dates of birth are read, and line up with names and nationalities")


def test_missing_dob_leaves_a_gap_not_a_shift() -> None:
    """A director without a published DOB must not shift the others along."""
    _stub([([_officer("OKAFOR, Jane", year=1985, month=3),
             _officer("NOBODY, Anon"),
             _officer("SMITH, Alan", year=1972, month=11)], 3)])
    d = run_pipeline.get_company_details("00000002")
    assert d["director_dobs"] == "1985-03; ; 1972-11", d["director_dobs"]
    assert len(d["director_names"].split(";")) == len(d["director_dobs"].split(";"))
    print("  OK  a director with no published DOB keeps its position in the list")


def test_gender_is_never_claimed() -> None:
    """Companies House has no gender field, so nothing here may produce one."""
    _stub([([_officer("OKAFOR, Jane", year=1985, month=3)], 1)])
    d = run_pipeline.get_company_details("00000003")
    assert "director_genders" not in d, "it invented a gender field"
    assert not any("gender" in k for k in d), d.keys()
    print("  OK  no gender is produced, because the source has none")


def test_serving_directors_found_behind_a_page_of_leavers() -> None:
    """First page all resigned; the serving director is on page two."""
    page1 = [_officer(f"OLD, Person{i}", resigned="2015-01-01") for i in range(100)]
    page2 = [_officer("CURRENT, Director", year=1990, month=6)]
    calls = _stub([(page1, 101), (page2, 101)])
    d = run_pipeline.get_company_details("00000004")
    assert d["director_names"] == "CURRENT, Director", d["director_names"]
    assert d["officers_seen"] == 101, d["officers_seen"]
    officer_calls = [c for c in calls if c[0].endswith("/officers")]
    assert len(officer_calls) == 2, f"it never paged: {officer_calls}"
    assert officer_calls[1][1]["start_index"] == 100, officer_calls[1][1]
    print("  OK  a serving director behind a full page of leavers is still found")


def test_resigned_and_non_directors_are_excluded() -> None:
    _stub([([_officer("OKAFOR, Jane", year=1985, month=3),
             _officer("GONE, Harry", resigned="2021-05-05"),
             _officer("SEC, Retary", role="secretary")], 3)])
    d = run_pipeline.get_company_details("00000005")
    assert d["director_names"] == "OKAFOR, Jane", d["director_names"]
    print("  OK  resigned officers and secretaries are excluded")


def test_details_done_means_the_lookup_ran() -> None:
    import api_v2
    typed_only = {"Company Name": "SECOND BUILD LTD"}
    fetched = {"Company Name": "SECOND BUILD LTD", "Status": "active",
               "Address": "12 Dock Road, Liverpool"}
    assert api_v2._compute_stages("00000006", typed_only)["details"] is False, \
        "a hand-typed name alone still counts as a completed lookup"
    assert api_v2._compute_stages("00000006", fetched)["details"] is True
    print("  OK  the stage is Done only once Companies House has actually been read")


def main() -> int:
    tests = [
        test_dobs_are_read_from_companies_house,
        test_missing_dob_leaves_a_gap_not_a_shift,
        test_gender_is_never_claimed,
        test_serving_directors_found_behind_a_page_of_leavers,
        test_resigned_and_non_directors_are_excluded,
        test_details_done_means_the_lookup_ran,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
