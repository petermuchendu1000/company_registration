"""
Where a company's files live, and who is allowed to see them.

Stage state is read off the filesystem: `app` is done when an .apk is
present, `certificate` when a PDF is, `director_id` when ID Front.jpeg is.
The directory those files live in used to be keyed on the company number and
name alone:

    pipeline_output/companies/{cn} - {NAME}/

Nothing in that path recorded the owning tenant, and nothing tied it to the
database row. Two faults followed, both reproduced below against the old
layout before being fixed:

  1. A directory left behind by an earlier run — a wiped database, a
     restored backup, a copy between machines — was picked up by whatever
     company next happened to have the same number and name. A brand-new
     company came up with its stages already done and someone else's
     artifacts listed as its own.

  2. auth.tenant_company_file authorised a download by asking whether the
     signed-in tenant held a *row* for that company number. The primary key
     is (tenant_id, company_number), so two tenants can both hold company
     20000001 — and both then passed that check against the same physical
     file. One tenant's AAB, certificate and director ID were served to the
     other.

These need a real Postgres, since tenancy and row ownership are the subject.

    DATABASE_URL=... python test_company_paths.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
    print("DATABASE_URL not set — skipping (these need a real Postgres).")
    raise SystemExit(0)

import db
import company_paths

# These tests create, move and delete company directories. Point the output
# root at a throwaway tree first: an earlier version of this file ran against
# the working tree and deleted a real directory during cleanup.
_TMP_OUTPUT = tempfile.mkdtemp(prefix="company-paths-test-")
os.environ["PIPELINE_OUTPUT_DIR"] = _TMP_OUTPUT


# ── fixtures ─────────────────────────────────────────────────────────────────

_MADE: list[int] = []


def _tenant(prefix: str) -> int:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    tid = db.create_tenant(slug, slug)
    _MADE.append(tid)
    return tid


def _company(tid: int, cn: str, name: str) -> None:
    db.add_company(tid, cn, name, "2020-01-01")
    db.update_row(tid, cn, {"Company Name": name})


def _touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


CN = "20000001"
NAME = "HARBOUR COURIERS LTD"


# ── 1. the directory is scoped to a tenant ───────────────────────────────────

def test_two_tenants_get_different_directories() -> None:
    a, b = _tenant("alpha"), _tenant("beta")
    with db.tenant_scope(a):
        da = company_paths.company_dir(CN, NAME)
    with db.tenant_scope(b):
        dbir = company_paths.company_dir(CN, NAME)
    assert da != dbir, f"both tenants resolved to {da}"
    assert da.parent != dbir.parent, (da.parent, dbir.parent)
    print("  OK  two tenants holding the same company number get separate directories")


def test_a_leftover_directory_is_not_adopted_by_a_new_tenant() -> None:
    """Fault 1: the stale-folder case, as actually hit."""
    a, b = _tenant("alpha"), _tenant("beta")
    _company(a, CN, NAME)
    _company(b, CN, NAME)

    with db.tenant_scope(a):
        built = company_paths.ensure_company_dir(CN, NAME)
        _touch(built / "app" / "artifacts" / "app-release.apk")

    with db.tenant_scope(b):
        theirs = company_paths.company_dir(CN, NAME)
        arts = theirs / "app" / "artifacts"
        found = list(arts.glob("*.apk")) if arts.is_dir() else []
    assert not found, f"the second tenant inherited {found}"
    print("  OK  a directory built by one tenant is invisible to another with the same company")


def test_stage_state_is_not_inherited_from_a_leftover_directory() -> None:
    """The user-visible symptom: a new company showing stages already done."""
    import api_v2
    a, b = _tenant("alpha"), _tenant("beta")
    _company(a, CN, NAME)
    _company(b, CN, NAME)

    with db.tenant_scope(a):
        built = company_paths.ensure_company_dir(CN, NAME)
        _touch(built / "app" / "artifacts" / "app-release.apk")
        _touch(built / "certificate" / f"Certificate_{CN}.pdf", b"%PDF-1.4" + b"0" * 2048)
        _touch(built / "director_id" / "ID Front.jpeg")
        mine = api_v2._compute_stages(CN, {"Company Name": NAME, "Status": "Active"})

    with db.tenant_scope(b):
        theirs = api_v2._compute_stages(CN, {"Company Name": NAME, "Status": "Active"})

    assert mine["app"] and mine["certificate"] and mine["director_id"], mine
    for stage in ("app", "certificate", "director_id"):
        assert theirs[stage] is False, f"{stage} was inherited from another tenant: {theirs}"
    print("  OK  filesystem-derived stages do not carry across tenants")


def test_glob_lookups_stay_inside_the_tenant() -> None:
    """play_publisher and policy_gate glob on the number when the name is unknown."""
    import play_publisher
    from apps.generator import policy_gate
    a, b = _tenant("alpha"), _tenant("beta")
    _company(a, CN, NAME)
    _company(b, CN, NAME)

    with db.tenant_scope(a):
        built = company_paths.ensure_company_dir(CN, NAME)
        _touch(built / "app" / "artifacts" / "app-release.aab")
        assert play_publisher._app_dir(CN) is not None, "the owner's own build was not found"
        assert policy_gate.app_dir_for(CN) is not None

    with db.tenant_scope(b):
        assert play_publisher._app_dir(CN) is None, "the publisher globbed into another tenant"
        assert policy_gate.app_dir_for(CN) is None, "the policy gate globbed into another tenant"
    print("  OK  number-only lookups do not reach another tenant's subtree")


# ── 2. downloads ─────────────────────────────────────────────────────────────

def test_download_guard_refuses_another_tenants_file() -> None:
    """
    Fault 2. Both tenants hold a row for the number, so the row check that
    used to be the whole guard passes for both.
    """
    from auth import tenant_company_file
    a, b = _tenant("alpha"), _tenant("beta")
    _company(a, CN, NAME)
    _company(b, CN, NAME)

    with db.tenant_scope(a):
        built = company_paths.ensure_company_dir(CN, NAME)
        aab = _touch(built / "app" / "artifacts" / "app-release.aab")
        assert db.find_row(a, CN) is not None
        assert tenant_company_file(str(aab)) is not None, "the owner was denied its own file"

    with db.tenant_scope(b):
        assert db.find_row(b, CN) is not None, "precondition: the row check would pass"
        assert tenant_company_file(str(aab)) is None, \
            "another tenant's AAB was served"
    print("  OK  a download is refused to a tenant that holds the row but not the file")


def test_download_guard_refuses_a_foreign_stamped_dir_in_our_own_subtree() -> None:
    """
    The case the subtree check cannot see: a directory sitting in this
    tenant's own folder but stamped for another — a restored backup, a copy
    between machines, a slug reused after a tenant was recreated. Without
    the stamp check in tenant_company_file, this file is served.
    """
    from auth import tenant_company_file
    a, b = _tenant("alpha"), _tenant("beta")
    _company(b, CN, NAME)
    with db.tenant_scope(b):
        d = company_paths.ensure_company_dir(CN, NAME)
        f = _touch(d / "app" / "artifacts" / "app-release.aab")
        assert tenant_company_file(str(f)) is not None, "precondition: b can read its own file"
        # Now the directory claims to belong to tenant a, though it sits in b's.
        (d / company_paths.STAMP_NAME).write_text(
            f'{{"tenant_id": {a}, "company_number": "{CN}"}}', encoding="utf-8")
        assert tenant_company_file(str(f)) is None, \
            "a directory stamped for another tenant was served from our own subtree"
    print("  OK  a foreign-stamped directory inside our subtree is refused by the download guard")


def test_download_guard_still_refuses_paths_outside_the_tree() -> None:
    from auth import tenant_company_file
    a = _tenant("alpha")
    _company(a, CN, NAME)
    with db.tenant_scope(a):
        assert tenant_company_file("/etc/passwd") is None
        assert tenant_company_file("") is None
        outside = _touch(company_paths.companies_root().parent / "escape.txt")
        assert tenant_company_file(str(outside)) is None
        assert tenant_company_file(str(company_paths.companies_root() / ".." / "escape.txt")) is None
    print("  OK  traversal and out-of-tree paths are still refused")


# ── 3. the ownership stamp ───────────────────────────────────────────────────

def test_stamp_records_the_owner() -> None:
    a = _tenant("alpha")
    with db.tenant_scope(a):
        d = company_paths.ensure_company_dir(CN, NAME)
        stamp = company_paths.read_stamp(d)
    assert stamp and stamp["tenant_id"] == a, stamp
    assert stamp["company_number"] == CN, stamp
    print("  OK  a company directory records the tenant that created it")


def test_a_foreign_stamp_is_rejected_even_inside_the_subtree() -> None:
    """Defence in depth: a directory copied into the wrong subtree."""
    a, b = _tenant("alpha"), _tenant("beta")
    _company(b, CN, NAME)
    with db.tenant_scope(b):
        d = company_paths.ensure_company_dir(CN, NAME)
        f = _touch(d / "app" / "artifacts" / "app-release.aab")
        # Overwrite the stamp so the directory claims to belong to tenant a.
        (d / company_paths.STAMP_NAME).write_text(
            f'{{"tenant_id": {a}, "company_number": "{CN}"}}', encoding="utf-8")
        company_paths._reset_cache()
        assert company_paths.belongs_to(f) is False, "a foreign stamp was accepted"
    print("  OK  a directory stamped for another tenant is rejected inside the subtree")


def test_unstamped_directory_in_the_subtree_is_accepted() -> None:
    """Migrated and hand-made directories have no stamp; they must still work."""
    a = _tenant("alpha")
    _company(a, CN, NAME)
    with db.tenant_scope(a):
        d = company_paths.company_dir(CN, NAME)
        f = _touch(d / "app" / "artifacts" / "app-release.aab")
        assert not (d / company_paths.STAMP_NAME).exists()
        assert company_paths.belongs_to(f) is True
    print("  OK  an unstamped directory inside the tenant's own subtree still resolves")


# ── 4. migration off the old layout ──────────────────────────────────────────

def test_legacy_directory_moves_to_its_owner() -> None:
    a = _tenant("alpha")
    _company(a, CN, NAME)
    legacy = company_paths.companies_root() / f"{CN} - {NAME}"
    _touch(legacy / "app" / "artifacts" / "app-release.aab")
    try:
        with db.tenant_scope(a):
            notes = company_paths.migrate_legacy(a)
            moved = company_paths.company_dir(CN, NAME)
            assert (moved / "app" / "artifacts" / "app-release.aab").exists(), notes
            assert not legacy.exists(), "the legacy directory was left behind"
            assert company_paths.read_stamp(moved), "the moved directory was not stamped"
    finally:
        import shutil
        shutil.rmtree(legacy, ignore_errors=True)
    print("  OK  a pre-tenant directory is moved into its owner's subtree and stamped")


def test_legacy_directory_is_not_handed_to_a_second_claimant() -> None:
    """
    Two tenants hold the number; only the first migration takes the files.
    The second must start empty rather than inherit them.
    """
    a, b = _tenant("alpha"), _tenant("beta")
    _company(a, CN, NAME)
    _company(b, CN, NAME)
    legacy = company_paths.companies_root() / f"{CN} - {NAME}"
    _touch(legacy / "app" / "artifacts" / "app-release.aab")
    try:
        company_paths.migrate_legacy(a)
        company_paths.migrate_legacy(b)
        with db.tenant_scope(b):
            theirs = company_paths.company_dir(CN, NAME)
        assert not (theirs / "app" / "artifacts" / "app-release.aab").exists(), \
            "the second claimant inherited the legacy files"
    finally:
        import shutil
        shutil.rmtree(legacy, ignore_errors=True)
    print("  OK  a legacy directory goes to one tenant, not to everyone holding the number")


def test_migration_refuses_to_move_into_the_fallback_segment() -> None:
    """
    If the tenant cannot be resolved, slug_for falls back to UNTENANTED.
    Migrating into it would move a real tenant's files somewhere nothing
    looks for them — observed for real before this guard existed.
    """
    import shutil
    legacy = company_paths.companies_root() / f"{CN} - {NAME}"
    fallback = company_paths.companies_root() / company_paths.UNTENANTED / f"{CN} - {NAME}"
    shutil.rmtree(fallback, ignore_errors=True)   # this test owns that path
    _touch(legacy / "app" / "artifacts" / "app-release.aab")
    original = company_paths.slug_for
    try:
        company_paths.slug_for = lambda tenant_id=None: company_paths.UNTENANTED
        a = _tenant("alpha")
        _company(a, CN, NAME)
        company_paths.migrate_legacy(a)
        assert legacy.exists(), "files were moved out of the legacy location"
        assert not fallback.exists(), "files were moved into the fallback segment"
    finally:
        company_paths.slug_for = original
        shutil.rmtree(legacy, ignore_errors=True)
    print("  OK  migration refuses to move files into the untenanted fallback")


def test_migration_leaves_an_unowned_directory_alone() -> None:
    a = _tenant("alpha")          # holds no company at all
    legacy = company_paths.companies_root() / "29999999 - SOMEONE ELSE LTD"
    _touch(legacy / "app" / "x.txt")
    try:
        company_paths.migrate_legacy(a)
        assert legacy.exists(), "a directory this tenant does not own was moved"
    finally:
        import shutil
        shutil.rmtree(legacy, ignore_errors=True)
    print("  OK  migration does not touch a directory the tenant has no row for")


def _cleanup() -> None:
    shutil.rmtree(_TMP_OUTPUT, ignore_errors=True)


def main() -> int:
    tests = [
        test_two_tenants_get_different_directories,
        test_a_leftover_directory_is_not_adopted_by_a_new_tenant,
        test_stage_state_is_not_inherited_from_a_leftover_directory,
        test_glob_lookups_stay_inside_the_tenant,
        test_download_guard_refuses_another_tenants_file,
        test_download_guard_refuses_a_foreign_stamped_dir_in_our_own_subtree,
        test_download_guard_still_refuses_paths_outside_the_tree,
        test_stamp_records_the_owner,
        test_a_foreign_stamp_is_rejected_even_inside_the_subtree,
        test_unstamped_directory_in_the_subtree_is_accepted,
        test_legacy_directory_moves_to_its_owner,
        test_legacy_directory_is_not_handed_to_a_second_claimant,
        test_migration_refuses_to_move_into_the_fallback_segment,
        test_migration_leaves_an_unowned_directory_alone,
    ]
    failures = 0
    for test in tests:
        company_paths._reset_cache()
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
    _cleanup()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
