"""
Guards on the one pipeline stage that spends money.

Registering a domain is an irreversible purchase. Two things had to be true
and were not:

  1. Re-running a completed domain stage bought a second domain. Nothing
     anywhere asked whether the company already had one, and the dashboard
     offers a re-run button on every completed stage.
  2. No funds check. get_balance() existed and was never called, so an empty
     Namecheap account failed at the purchase rather than before it.

These stub the Namecheap module, so no call leaves the machine and no money
can move while they run — which is the only responsible way to test this.

    python test_domain_guard.py
"""

from __future__ import annotations

import sys
import types

import run_pipeline


class FakeNamecheap:
    """Stands in for namecheap_automation. Records whether a purchase happened."""

    def __init__(self, balance="100.00", currency="USD", balance_error=None):
        self.purchases: list[str] = []
        self.searches: list[str] = []
        self._balance = balance
        self._currency = currency
        self._balance_error = balance_error

    def get_balance(self):
        if self._balance_error:
            return {"error": self._balance_error}
        return {"available": self._balance, "currency": self._currency}

    def find_cheapest_domain(self, company_name):
        self.searches.append(company_name)
        return {"best": {"domain": "newlybought.online", "available": True,
                         "is_premium": False}, "alternatives": []}

    def get_existing_account_contact(self, reference_domain=None, address_id=None):
        return {"FirstName": "A", "LastName": "B", "source": "test"}

    def register_domain(self, domain_name, years=1, registrant_info=None):
        self.purchases.append(domain_name)
        return {"registered": True, "domain": domain_name, "charged": "0.98",
                "domain_id": "1", "order_id": "1"}


def _install(fake: FakeNamecheap) -> None:
    mod = types.ModuleType("namecheap_automation")
    for name in ("get_balance", "find_cheapest_domain",
                 "get_existing_account_contact", "register_domain"):
        setattr(mod, name, getattr(fake, name))
    sys.modules["namecheap_automation"] = mod


def test_existing_domain_is_not_bought_over() -> None:
    fake = FakeNamecheap()
    _install(fake)
    r = run_pipeline.register_company_domain(
        "HARBOUR COURIERS LTD", {}, current_domain="harbour.online",
    )
    assert r["status"] == "exists", r
    assert r["domain"] == "harbour.online", r
    assert fake.purchases == [], f"it bought a domain anyway: {fake.purchases}"
    assert fake.searches == [], "it even went looking, which costs API quota"
    print("  OK  a company that already has a domain does not get a second one")


def test_first_registration_still_works() -> None:
    fake = FakeNamecheap()
    _install(fake)
    r = run_pipeline.register_company_domain("NEW CO LTD", {}, current_domain="")
    assert r["status"] == "registered", r
    assert fake.purchases == ["newlybought.online"], fake.purchases
    print("  OK  a company with no domain still gets one")


def test_additional_domain_requires_asking_for_it() -> None:
    fake = FakeNamecheap()
    _install(fake)
    r = run_pipeline.register_company_domain(
        "HARBOUR COURIERS LTD", {}, current_domain="harbour.online",
        allow_additional=True,
    )
    assert r["status"] == "registered", r
    assert fake.purchases == ["newlybought.online"], fake.purchases
    print("  OK  a second domain is possible, but only on purpose")


def test_empty_account_refuses_before_spending() -> None:
    fake = FakeNamecheap(balance="3.00")
    _install(fake)
    r = run_pipeline.register_company_domain("NEW CO LTD", {})
    assert r["status"] == "error", r
    assert "balance" in r["error"].lower(), r["error"]
    assert fake.purchases == [], fake.purchases
    assert fake.searches == [], "it searched before checking it could pay"
    print("  OK  an underfunded account is refused before anything is bought")


def test_unreadable_balance_does_not_block_the_purchase() -> None:
    """A balance call that fails is not evidence of no funds; warn and carry on."""
    fake = FakeNamecheap(balance_error="API down")
    _install(fake)
    r = run_pipeline.register_company_domain("NEW CO LTD", {})
    assert r["status"] == "registered", r
    assert fake.purchases == ["newlybought.online"], fake.purchases
    print("  OK  an unreadable balance warns rather than blocking")


def main() -> int:
    tests = [
        test_existing_domain_is_not_bought_over,
        test_first_registration_still_works,
        test_additional_domain_requires_asking_for_it,
        test_empty_account_refuses_before_spending,
        test_unreadable_balance_does_not_block_the_purchase,
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
