"""
Tenant isolation tests for db.py.

Needs a throwaway PostgreSQL database — these create and drop tables.
Never point this at production.

    createdb tenancy_test
    DATABASE_URL="postgresql://postgres@localhost/tenancy_test" python test_tenancy.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import uuid

import db


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_migration_from_pre_tenancy_table() -> None:
    """A table keyed on company_number alone migrates to the composite key."""
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS companies CASCADE")
        cur.execute(
            "CREATE TABLE companies ("
            "  row_no INTEGER,"
            "  company_number TEXT PRIMARY KEY,"
            "  company_name TEXT,"
            "  domain TEXT,"
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "INSERT INTO companies (row_no, company_number, company_name) "
            "VALUES (1, '13510663', 'Legacy Co Ltd')"
        )
        conn.commit()

    db.ensure_schema()

    with db._connect() as conn, conn.cursor() as cur:
        assert db._pk_columns(cur, "companies") == ["tenant_id", "company_number"]

    default = db.get_tenant_by_slug(db.DEFAULT_TENANT_SLUG)
    assert default is not None, "migration must create the default tenant"
    rows = db.all_rows(default["id"], use_cache=False)
    assert any(r["Company Name"] == "Legacy Co Ltd" for r in rows), \
        "pre-tenancy rows must survive and land in the default tenant"

    db.ensure_schema()  # idempotent


def test_same_company_in_two_tenants() -> None:
    a = db.create_tenant(_slug("a"))
    b = db.create_tenant(_slug("b"))
    assert db.add_company(a, "13510663", "Held by A") is True
    assert db.add_company(b, "13510663", "Held by B") is True, \
        "the composite key must let two tenants hold one company number"
    assert db.add_company(a, "13510663", "dupe") is False, \
        "a repeat within one tenant is still a duplicate"
    assert db.find_row(a, "13510663")["Company Name"] == "Held by A"
    assert db.find_row(b, "13510663")["Company Name"] == "Held by B"


def test_reads_and_writes_are_scoped() -> None:
    a = db.create_tenant(_slug("a"))
    b = db.create_tenant(_slug("b"))
    db.add_company(a, "11111111", "A Only")
    db.add_company(b, "22222222", "B Only")

    assert db.company_numbers(a) == {"11111111"}
    assert db.company_numbers(b) == {"22222222"}
    assert db.find_row(a, "22222222") is None, "must not see another tenant's company"

    db.update_row(a, "11111111", {"Domain": "a.example"})
    db.update_row(b, "22222222", {"Domain": "b.example"})
    assert db.find_row(a, "11111111")["Domain"] == "a.example"
    assert db.find_row(b, "22222222")["Domain"] == "b.example"

    # Writing to a company this tenant doesn't hold creates its own row
    # rather than touching the other tenant's.
    db.update_row(a, "22222222", {"Domain": "hijacked"})
    assert db.find_row(b, "22222222")["Domain"] == "b.example", \
        "a write must never reach another tenant's row"


def test_cache_does_not_leak_between_tenants() -> None:
    """The read cache has a TTL; two tenants reading inside it must not mix."""
    a = db.create_tenant(_slug("a"))
    b = db.create_tenant(_slug("b"))
    db.add_company(a, "33333333", "A Only")
    db.add_company(b, "44444444", "B Only")

    for _ in range(3):  # stay well inside the 1.5s TTL
        assert {r["Company Number"] for r in db.all_rows(a)} == {"33333333"}
        assert {r["Company Number"] for r in db.all_rows(b)} == {"44444444"}


def test_row_numbering_is_per_tenant() -> None:
    a = db.create_tenant(_slug("a"))
    db.add_company(a, "55555555", "First")
    db.add_company(a, "66666666", "Second")
    assert sorted(r["No."] for r in db.all_rows(a, use_cache=False)) == [1, 2]


def test_tenant_is_required() -> None:
    for bad in (None, 0, -1):
        try:
            db._tid(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"_tid must reject {bad!r}")

    db.set_current_tenant(None)
    os.environ.pop("SINGLE_TENANT_SLUG", None)
    try:
        db.current_tenant_id()
    except RuntimeError:
        pass
    else:
        raise AssertionError("an unbound tenant must raise, never default to one")


def test_tenant_scope_nests_and_restores() -> None:
    a = db.create_tenant(_slug("a"))
    b = db.create_tenant(_slug("b"))
    db.set_current_tenant(None)
    with db.tenant_scope(a):
        assert db.current_tenant_id() == a
        with db.tenant_scope(b):
            assert db.current_tenant_id() == b
        assert db.current_tenant_id() == a
    try:
        db.current_tenant_id()
    except RuntimeError:
        pass
    else:
        raise AssertionError("scope exit must leave the tenant unbound")


def test_users_and_auth() -> None:
    t = db.create_tenant(_slug("t"))
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    uid = db.create_user(t, f"  {email.upper()} ", "s3cret", "client")

    user = db.verify_user(email, "s3cret")
    assert user is not None and user["tenant_id"] == t
    assert "password_hash" not in user, "never hand the hash back to callers"
    assert db.verify_user(email, "wrong") is None
    assert db.verify_user("nobody@example.com", "s3cret") is None
    assert db.verify_user(email.upper(), "s3cret") is not None, "email is case-insensitive"

    db.set_password(uid, "rotated")
    assert db.verify_user(email, "s3cret") is None
    assert db.verify_user(email, "rotated") is not None

    try:
        db.create_user(t, "x@example.com", "pw", "superadmin")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown role must be rejected")


def test_operator_cross_tenant_view() -> None:
    a = db.create_tenant(_slug("a"))
    db.add_company(a, "77777777", "Visible To Operator")
    rows = db.all_rows_all_tenants()
    assert all("Tenant Id" in r for r in rows)
    assert any(r["Company Number"] == "77777777" for r in rows)


def test_legacy_orphan_column_is_rescued(tmp_name: str = "") -> None:
    """
    A populated cell whose column header was deleted must be carried over,
    not dropped. companies_pipeline.xlsx column 24 held an earlier
    Company Status; it belongs in Company Notes, without disturbing the
    current status.
    """
    import openpyxl

    tenant = db.create_tenant(_slug("orphan"))
    headers = [h for h, _ in db.COLUMNS]
    # Blank header at index 24, exactly like the legacy workbook.
    headers = headers[:24] + [""] + headers[24:]
    row = [""] * len(headers)
    row[headers.index("Company Number")] = "02591663"
    row[headers.index("Company Name")] = "Legacy Co"
    row[headers.index("Company Status")] = "Declined"
    row[24] = "Terminated"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(row)
    path = pathlib.Path(tempfile.gettempdir()) / f"orphan-{uuid.uuid4().hex[:8]}.xlsx"
    wb.save(path)
    try:
        report = db.unmapped_excel_values(str(path))
        assert len(report) == 1, report
        assert report[0]["rescued_into"] == "Company Notes", report[0]

        db.import_from_excel(tenant, str(path))
        got = db.find_row(tenant, "02591663")
        assert got["Company Notes"] == "Previous Company Status: Terminated", got["Company Notes"]
        assert got["Company Status"] == "Declined", "current status must survive untouched"

        # Re-importing must not append the note twice.
        db.import_from_excel(tenant, str(path))
        got = db.find_row(tenant, "02591663")
        assert got["Company Notes"] == "Previous Company Status: Terminated", "note duplicated"
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    if not (os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")):
        print("Set DATABASE_URL to a throwaway database first.")
        return 2

    db.ensure_schema()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {fn.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
