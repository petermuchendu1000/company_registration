"""
Domain matching: pairing domains already in the Namecheap account with
companies.

A wrong pairing here is not cosmetic. The domain becomes the app's website,
the dev@ forwarding target and the developer contact on the Play listing, so
a domain on the wrong company follows that company all the way to the store.

Four faults:

  1. It carried its own Namecheap client against api.namecheap.com hardcoded,
     so NAMECHEAP_SANDBOX was ignored, credentials came out as a bare
     KeyError, and a rate limit was not retried.
  2. One unpaged request. An account with more than 100 domains had the rest
     silently treated as though they were not registered.
  3. It printed "MATCH" and then wrote nothing whenever the best-scoring
     company already had a domain — a line claiming a pairing that never
     happened.
  4. Equal scores were resolved by whichever database row came back first.

Namecheap and the datastore are stubbed.

    python test_stage_match.py
"""

from __future__ import annotations

import sys
import types

import pipeline


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.writes: list[tuple[str, dict]] = []

    def current_tenant_id(self):
        return 1

    def all_rows(self, tenant_id):
        return self.rows

    def update_row(self, tenant_id, cn, updates):
        self.writes.append((cn, updates))


def _row(cn, name, domain=""):
    return {"Company Number": cn, "Company Name": name, "Domain": domain}


def _install(domains, error=None):
    mod = types.ModuleType("namecheap_automation")
    if error:
        mod.get_domain_list = lambda: {"error": error}
    else:
        mod.get_domain_list = lambda: {
            "domains": [{"name": d} for d in domains], "count": len(domains),
        }
    sys.modules["namecheap_automation"] = mod


def test_clear_match_is_written() -> None:
    _install(["harbourcouriers.online"])
    fake = FakeDb([_row("20000001", "HARBOUR COURIERS LTD")])
    pipeline.db = fake
    pipeline.stage_match()
    assert fake.writes == [("20000001", {"Domain": "harbourcouriers.online"})], fake.writes
    print("  OK  an unambiguous domain is assigned")


def test_best_match_already_has_a_domain_is_not_claimed() -> None:
    """The old code printed MATCH here and wrote nothing."""
    _install(["harbourcouriers.online"])
    fake = FakeDb([_row("20000001", "HARBOUR COURIERS LTD", "existing.online")])
    pipeline.db = fake
    pipeline.stage_match()
    assert fake.writes == [], f"it overwrote an existing domain: {fake.writes}"
    print("  OK  a company that already has a domain is reported, not silently skipped")


def test_two_equal_candidates_are_refused() -> None:
    """Two companies fitting equally well must not be decided by row order."""
    _install(["careservices.online"])
    rows = [_row("30000001", "CARE SERVICES LTD"), _row("30000002", "CARE SERVICES LTD")]
    fake = FakeDb(rows)
    pipeline.db = fake
    pipeline.stage_match()
    assert fake.writes == [], f"it guessed between equal candidates: {fake.writes}"

    # ...and the reverse row order must give the same answer.
    fake2 = FakeDb(list(reversed(rows)))
    pipeline.db = fake2
    pipeline.stage_match()
    assert fake2.writes == [], fake2.writes
    print("  OK  equally-scoring companies are refused, whatever order the rows arrive in")


def test_short_domain_core_does_not_match_on_substring_alone() -> None:
    """"abc" is a substring of a great many company names."""
    _install(["abc.online"])
    fake = FakeDb([_row("40000001", "FABCORP HOLDINGS LTD")])
    pipeline.db = fake
    pipeline.stage_match()
    assert fake.writes == [], f"a three-letter core matched on substring: {fake.writes}"
    print("  OK  a very short domain core no longer matches on substring alone")


def test_one_company_does_not_take_two_domains() -> None:
    _install(["harbourcouriers.online", "harbourcouriers.site"])
    fake = FakeDb([_row("20000001", "HARBOUR COURIERS LTD")])
    pipeline.db = fake
    pipeline.stage_match()
    assert len(fake.writes) == 1, f"it assigned twice: {fake.writes}"
    print("  OK  a company takes one domain, not every domain that fits it")


def test_dry_run_writes_nothing() -> None:
    _install(["harbourcouriers.online"])
    fake = FakeDb([_row("20000001", "HARBOUR COURIERS LTD")])
    pipeline.db = fake
    pipeline.stage_match(dry_run=True)
    assert fake.writes == [], fake.writes
    print("  OK  a dry run writes nothing")


def test_listing_failure_stops_rather_than_matching_on_partial_data() -> None:
    _install([], error="Failed on page 2: ['Rate limited']")
    fake = FakeDb([_row("20000001", "HARBOUR COURIERS LTD")])
    pipeline.db = fake
    pipeline.stage_match()
    assert fake.writes == [], "it matched against an incomplete domain list"
    print("  OK  a failed domain listing stops the stage instead of matching on part of it")


def test_domain_list_pagination() -> None:
    """The shared client must walk past the first page of 100."""
    # Earlier tests put a stub in sys.modules; this one needs the real thing.
    sys.modules.pop("namecheap_automation", None)
    import importlib
    real = importlib.import_module("namecheap_automation")
    pages: list[list[str]] = [
        [f"d{i}.online" for i in range(100)],
        [f"e{i}.online" for i in range(30)],
    ]
    calls: list[dict] = []

    class Node:
        def __init__(self, name):
            self.tag = "Domain"
            self.attrib = {"Name": name}

    class Root:
        def __init__(self, names):
            self._names = names

        def iter(self):
            return [Node(n) for n in self._names]

    def fake_api_call(command, extra_params=None, method="GET"):
        calls.append(dict(extra_params or {}))
        page = int((extra_params or {}).get("Page", 1))
        names = pages[page - 1] if page <= len(pages) else []
        return {"root": Root(names), "status": "OK", "errors": [], "raw": "", "ns": {}}

    original = real._api_call
    real._api_call = fake_api_call
    try:
        out = real.get_domain_list()
    finally:
        real._api_call = original

    assert out["count"] == 130, out["count"]
    assert len(calls) == 2, f"it stopped after one page: {calls}"
    assert calls[1]["Page"] == "2", calls[1]
    print("  OK  the domain list pages past the first 100")


def test_search_query_is_sent_as_a_parameter() -> None:
    """A company name with "&" in it must not truncate the search."""
    import api_v2
    seen: list[tuple[str, dict]] = []

    class StubRunPipeline:
        @staticmethod
        def ch_get(path, params=None):
            seen.append((path, dict(params or {})))
            return {"items": []}

    sys.modules["run_pipeline"] = StubRunPipeline
    original_rows = api_v2._all_rows
    api_v2._all_rows = lambda: []
    try:
        import app as flask_app
        with flask_app.app.test_request_context("/api/v2/company/search?q=H %26 M LTD"):
            api_v2.v2_search()
    finally:
        api_v2._all_rows = original_rows
        sys.modules.pop("run_pipeline", None)

    assert seen, "Companies House was never queried"
    path, params = seen[0]
    assert "?" not in path, f"the query is still baked into the path: {path}"
    assert params.get("q") == "H & M LTD", params
    assert params.get("items_per_page") == 20, params
    print("  OK  a company name containing & reaches Companies House intact")


def main() -> int:
    import db as real_db
    tests = [
        test_clear_match_is_written,
        test_best_match_already_has_a_domain_is_not_claimed,
        test_two_equal_candidates_are_refused,
        test_short_domain_core_does_not_match_on_substring_alone,
        test_one_company_does_not_take_two_domains,
        test_dry_run_writes_nothing,
        test_listing_failure_stops_rather_than_matching_on_partial_data,
        test_domain_list_pagination,
        test_search_query_is_sent_as_a_parameter,
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
        finally:
            pipeline.db = real_db
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
