"""
Where a company's generated files live.

Until now eight modules each rebuilt this path for themselves, and every one
of them keyed it on the company number and name alone:

    pipeline_output/companies/{cn} - {NAME}/

Nothing in that path records which tenant the files belong to, and nothing
links them to the database row that owns them. Two consequences, both real:

  * Stage state is read off the filesystem — `app` is done if an .apk is
    present, `certificate` if a PDF is. A directory left behind by an
    earlier run, a restored backup, or a different tenant holding the same
    company number makes a brand-new company come up already finished.

  * `auth.tenant_company_file` authorises a download by asking whether the
    signed-in tenant has a *row* for that company number. Two tenants can
    both hold company 20000001 — the composite primary key is
    (tenant_id, company_number) — and both then pass that check against the
    same physical file, so one tenant's AAB, certificate and director ID are
    served to the other.

So the tenant goes in the path:

    pipeline_output/companies/{tenant-slug}/{cn} - {NAME}/

and each company directory carries a stamp naming its owner, so a directory
that arrives by some route other than this module (a copy between machines,
a restored backup, manual surgery) can still be told apart from one this
tenant created.

Everything that touches a company directory goes through here. There is one
definition of the layout, not eight.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parent

#: Where generated output lives. Resolved per call rather than pinned at
#: import so it can be pointed at a data volume (PIPELINE_OUTPUT_DIR), and so
#: a test can redirect it without writing into the working tree.
_DEFAULT_OUTPUT = ROOT / "pipeline_output"


def output_root() -> Path:
    override = (os.environ.get("PIPELINE_OUTPUT_DIR") or "").strip()
    return Path(override).expanduser() if override else _DEFAULT_OUTPUT


def companies_root() -> Path:
    return output_root() / "companies"

#: Written inside each company directory; names the tenant that owns it.
STAMP_NAME = ".company.json"

#: Used when no tenant can be resolved at all. A directory under this segment
#: is never served to a signed-in tenant; it exists so that an unconfigured
#: CLI run writes somewhere inert instead of into a real tenant's tree.
UNTENANTED = "_untenanted"

_SLUG_CACHE: dict[int, str] = {}


# ── naming ───────────────────────────────────────────────────────────────────

def safe_name(name: str) -> str:
    """Strip the characters that are illegal in a path component."""
    return re.sub(r'[\\/:*?"<>|]', "", str(name or "")).strip()


def cn8(cn: str) -> str:
    """Companies House numbers are eight characters, zero-padded."""
    cn = str(cn or "").strip()
    return cn.zfill(8) if cn.isdigit() else cn


def folder_name(cn: str, name: str = "") -> str:
    safe = safe_name(name)
    return f"{cn8(cn)} - {safe}" if safe else cn8(cn)


# ── tenant segment ───────────────────────────────────────────────────────────

def slug_for(tenant_id: int | None = None) -> str:
    """
    The path segment for a tenant. Falls back to UNTENANTED rather than
    raising: a path helper that throws would turn a missing configuration
    into a crash in the middle of a build.
    """
    try:
        if tenant_id is None:
            tenant_id = db.current_tenant_id()
    except Exception:
        return UNTENANTED
    if tenant_id is None:
        return UNTENANTED
    tenant_id = int(tenant_id)
    cached = _SLUG_CACHE.get(tenant_id)
    if cached:
        return cached
    try:
        tenant = db.get_tenant(tenant_id)
    except Exception:
        return UNTENANTED
    slug = safe_name((tenant or {}).get("slug") or "") or f"t{tenant_id}"
    _SLUG_CACHE[tenant_id] = slug
    return slug


def tenant_root(tenant_id: int | None = None) -> Path:
    return companies_root() / slug_for(tenant_id)


# ── the directory ────────────────────────────────────────────────────────────

def company_dir(cn: str, name: str = "", tenant_id: int | None = None) -> Path:
    """This tenant's directory for a company. Does not create it."""
    return tenant_root(tenant_id) / folder_name(cn, name)


def find_company_dir(cn: str, tenant_id: int | None = None) -> Path | None:
    """
    Locate this tenant's directory for a company when the name is unknown.

    Callers used to glob the whole companies root on the number, which is
    what let one tenant's directory answer for another's. The glob is now
    inside the tenant's own subtree.
    """
    base = tenant_root(tenant_id)
    if not base.is_dir():
        return None
    target = cn8(cn)
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child.name == target or child.name.startswith(f"{target} - ")):
            return child
    return None


def ensure_company_dir(cn: str, name: str = "", tenant_id: int | None = None) -> Path:
    """Create this tenant's directory for a company, stamped with its owner."""
    path = company_dir(cn, name, tenant_id)
    path.mkdir(parents=True, exist_ok=True)
    write_stamp(path, cn, name, tenant_id)
    return path


# ── ownership stamp ──────────────────────────────────────────────────────────

def write_stamp(path: Path, cn: str, name: str = "", tenant_id: int | None = None) -> None:
    """Record who owns a company directory. Never overwrites an existing stamp."""
    stamp = path / STAMP_NAME
    if stamp.exists():
        return
    try:
        if tenant_id is None:
            tenant_id = db.current_tenant_id()
    except Exception:
        tenant_id = None
    try:
        stamp.write_text(json.dumps({
            "tenant_id": int(tenant_id) if tenant_id is not None else None,
            "tenant_slug": slug_for(tenant_id),
            "company_number": cn8(cn),
            "company_name": str(name or ""),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
    except OSError:
        pass  # a missing stamp is a soft failure; the path segment still isolates


def read_stamp(path: Path) -> dict | None:
    try:
        return json.loads((path / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def belongs_to(path: Path, tenant_id: int | None = None) -> bool:
    """
    Whether a company directory is this tenant's.

    The path segment is the real boundary; the stamp is a second check for a
    directory that arrived some other way. An unstamped directory sitting in
    the tenant's own subtree is accepted — that is what every directory
    created before this module looked like after migration.
    """
    try:
        resolved = path.resolve()
        base = tenant_root(tenant_id).resolve()
    except OSError:
        return False
    if not (resolved == base or resolved.is_relative_to(base)):
        return False
    # Walk up to the company directory itself, which is the stamped level.
    co = resolved
    while co.parent != base and co.parent != co:
        co = co.parent
    stamp = read_stamp(co)
    if stamp is None:
        return True
    owner = stamp.get("tenant_id")
    if owner is None:
        return True
    try:
        want = db.current_tenant_id() if tenant_id is None else tenant_id
    except Exception:
        return False
    return int(owner) == int(want)


# ── migration ────────────────────────────────────────────────────────────────

def migrate_legacy(tenant_id: int | None = None) -> list[str]:
    """
    Move pre-tenant directories into the tenant's subtree.

    A legacy directory sits directly under pipeline_output/companies/ and is
    named "{cn}" or "{cn} - {name}". It is moved only when this tenant holds
    a row for that company number and the destination does not already
    exist, so a second tenant holding the same number never inherits files
    it did not create — it starts empty, which is the correct answer.

    Returns a line per directory considered, for the caller to log.
    """
    notes: list[str] = []
    if not companies_root().is_dir():
        return notes
    try:
        tid = db.current_tenant_id() if tenant_id is None else int(tenant_id)
    except Exception:
        return notes
    dest_root = tenant_root(tid)
    # Never migrate into the fallback segment. If the tenant cannot be
    # resolved we do not know who owns these files, and moving them under
    # _untenanted would hide them from the tenant that does.
    if dest_root.name == UNTENANTED:
        return notes

    for child in sorted(companies_root().iterdir()):
        if not child.is_dir():
            continue
        # Tenant subtrees are directories whose name is not "{cn}..." — skip
        # anything that is already a tenant segment.
        head = child.name.split(" - ", 1)[0].strip()
        if not head.isdigit():
            continue
        try:
            owned = db.find_row(tid, head) is not None or db.find_row(tid, cn8(head)) is not None
        except Exception:
            owned = False
        if not owned:
            continue          # another tenant's, or nobody's — say nothing
        dest = dest_root / child.name
        if dest.exists():
            notes.append(f"left in place, destination already exists: {child.name}")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            child.rename(dest)
            write_stamp(dest, head, child.name.partition(" - ")[2], tid)
            notes.append(f"moved into {dest_root.name}/: {child.name}")
        except OSError as e:
            notes.append(f"could not move {child.name}: {e}")
    return notes


def migrate_all() -> list[str]:
    """
    Run the legacy migration for every tenant, once, at startup.

    Doing it here rather than lazily inside company_dir() keeps the filesystem
    scan and the row lookups off a path that is called in tight loops.
    """
    notes: list[str] = []
    try:
        tenants = db.list_tenants()
    except Exception:
        return notes
    for tenant in tenants:
        try:
            tid = int(tenant["id"])
        except (KeyError, TypeError, ValueError):
            continue
        for note in migrate_legacy(tid):
            notes.append(f"[{tenant.get('slug') or tid}] {note}")
    return notes


def _reset_cache() -> None:
    """Tests rebind tenants freely; the slug cache must not outlive that."""
    _SLUG_CACHE.clear()
