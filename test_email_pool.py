"""
The email pool: tenant isolation and concurrent assignment.

Both were broken while the pool was a shared emails.xlsx keyed by company
number alone. Measured against that implementation, eight threads assigning
at once produced one assignment and seven `BadZipFile: Truncated file header`
crashes, because each caller rewrote the whole workbook while others were
reading it. Stages run in threads, so that was reachable in normal use.

Needs a real Postgres: the fix is FOR UPDATE SKIP LOCKED, which a fake would
not exercise.

    DATABASE_URL=postgresql://... python test_email_pool.py
"""

from __future__ import annotations

import os
import threading

import db
from email_pool import EmailPool, PoolExhausted, extract_name_from_email

TENANTS: dict[str, int] = {}


def _tenant(slug: str) -> int:
    if slug not in TENANTS:
        if not db.get_tenant_by_slug(slug):
            db.create_tenant(slug, slug)
        TENANTS[slug] = db.get_tenant_by_slug(slug)["id"]
    return TENANTS[slug]


def _reset(tenant_id: int) -> None:
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {db.EMAIL_POOL_TABLE} WHERE tenant_id = %s", (tenant_id,))
        conn.commit()


def test_concurrent_assignment_gives_everyone_a_distinct_address() -> None:
    """Eight companies claiming at once: eight assignments, no duplicates."""
    tid = _tenant("mailpool-a")
    _reset(tid)
    db.import_emails(tid, [f"first.last{i}@gmail.com" for i in range(20)])

    got: list[tuple[str, str]] = []
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def claim(n: int) -> None:
        barrier.wait()
        try:
            email, _, _ = EmailPool(tenant_id=tid).assign_next(
                company_number=f"9000000{n}", company_name=f"Co {n}",
            )
            with lock:
                got.append((f"9000000{n}", email))
        except Exception as e:  # noqa: BLE001 - recorded and asserted on
            with lock:
                errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"assignment raised: {errors}"
    assert len(got) == 8, f"only {len(got)} of 8 companies were assigned"
    emails = [e for _, e in got]
    assert len(set(emails)) == 8, f"an address went to more than one company: {emails}"
    print("  OK  eight simultaneous claims get eight distinct addresses")


def test_assignment_is_idempotent_per_company() -> None:
    tid = _tenant("mailpool-a")
    pool = EmailPool(tenant_id=tid)
    before = pool.available_count
    first, _, _ = pool.assign_next(company_number="91000001", company_name="Repeat Ltd")
    again, _, _ = pool.assign_next(company_number="91000001", company_name="Repeat Ltd")
    assert first == again, f"second call handed out a different address: {first} vs {again}"
    assert pool.available_count == before - 1, "a repeat call consumed a second address"
    print("  OK  asking twice for one company does not consume two addresses")


def test_tenants_have_separate_pools() -> None:
    """Same company number in two tenants: different addresses, no visibility."""
    a, b = _tenant("mailpool-a"), _tenant("mailpool-b")
    _reset(a)
    _reset(b)
    db.import_emails(a, ["alpha.one@gmail.com", "alpha.two@gmail.com"])
    db.import_emails(b, ["beta.one@gmail.com", "beta.two@gmail.com"])

    cn = "92000001"
    a_email, _, _ = EmailPool(tenant_id=a).assign_next(company_number=cn, company_name="A Ltd")
    b_email, _, _ = EmailPool(tenant_id=b).assign_next(company_number=cn, company_name="B Ltd")

    assert a_email.startswith("alpha"), a_email
    assert b_email.startswith("beta"), b_email
    assert a_email != b_email, "the same address went to two tenants"

    seen_by_b, _, _ = EmailPool(tenant_id=b).get_assigned_email(cn)
    assert seen_by_b == b_email, f"tenant B reads back {seen_by_b}, not its own address"
    assert EmailPool(tenant_id=a).available_count == 1, "tenant B consumed tenant A's pool"
    print("  OK  pools are per tenant, and neither can read the other's")


def test_exhaustion_is_reported_not_guessed() -> None:
    tid = _tenant("mailpool-c")
    _reset(tid)
    db.import_emails(tid, ["only.one@gmail.com"])
    pool = EmailPool(tenant_id=tid)
    pool.assign_next(company_number="93000001", company_name="First Ltd")
    try:
        pool.assign_next(company_number="93000002", company_name="Second Ltd")
    except PoolExhausted as e:
        assert "all 1 addresses" in str(e), str(e)
        assert isinstance(e, RuntimeError), "existing handlers catch RuntimeError"
        print("  OK  an exhausted pool says so, and still raises RuntimeError")
        return
    raise AssertionError("expected PoolExhausted")


def test_release_returns_the_address() -> None:
    tid = _tenant("mailpool-c")
    pool = EmailPool(tenant_id=tid)
    before = pool.available_count
    assert pool.release("93000001") is True
    assert pool.available_count == before + 1
    assert pool.get_assigned_email("93000001") == (None, None, None)
    print("  OK  releasing a company's address puts it back in the pool")


def test_import_is_idempotent() -> None:
    tid = _tenant("mailpool-c")
    first = db.import_emails(tid, ["dup.one@gmail.com", "dup.two@gmail.com"])
    second = db.import_emails(tid, ["dup.one@gmail.com", "dup.two@gmail.com"])
    assert first == 2, first
    assert second == 0, f"re-importing added {second} duplicate rows"
    print("  OK  importing the same file twice adds nothing the second time")


def test_name_extraction_unchanged() -> None:
    assert extract_name_from_email("john.doe@gmail.com") == ("John", "Doe")
    assert extract_name_from_email("a@gmail.com") == ("A", "User")
    print("  OK  name extraction still behaves as before")


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL not set — skipping (the pool lives in Postgres).")
        return 0
    db.ensure_schema()
    tests = [
        test_concurrent_assignment_gives_everyone_a_distinct_address,
        test_assignment_is_idempotent_per_company,
        test_tenants_have_separate_pools,
        test_exhaustion_is_reported_not_guessed,
        test_release_returns_the_address,
        test_import_is_idempotent,
        test_name_extraction_unchanged,
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
