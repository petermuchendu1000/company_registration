"""
Supabase (PostgreSQL) data-access layer — the single source of truth for the
company pipeline. Replaces the old Excel-backed store.

All rows are exchanged as dicts keyed by the SAME human header labels the rest
of the codebase already uses (e.g. "Company Number", "Company Name",
"Date of Creation"), so callers don't need to know about SQL columns.

Connection string is read from the environment (DATABASE_URL, falling back to
SUPABASE_DB_URL). Never hard-code credentials — put them in .env (git-ignored).

Multi-tenancy
-------------
Every company belongs to exactly one tenant, and `tenant_id` is a REQUIRED
first argument on every data function. There is deliberately no default: a
call site that forgets it raises TypeError instead of quietly returning
another client's companies. The primary key is (tenant_id, company_number),
so two tenants can hold the same UK company without colliding.

Cross-tenant reads exist only through the explicitly named
`*_all_tenants()` helpers, which are for operator/admin views.

Public API:
    ensure_schema()                          -> create/migrate all tables

    tenants:
    create_tenant(slug, name)                -> int (tenant id)
    get_tenant(tenant_id) / get_tenant_by_slug(slug) -> dict | None
    list_tenants()                           -> list[dict]

    users:
    create_user(tenant_id, email, password, role="client") -> int
    verify_user(email, password)             -> dict | None
    get_user(user_id)                        -> dict | None

    companies (all tenant-scoped):
    all_rows(tenant_id)                      -> list[dict] keyed by header labels
    find_row(tenant_id, cn)                  -> dict | None
    add_company(tenant_id, cn, name, date="")-> bool (True if inserted)
    update_row(tenant_id, cn, {header: val}) -> None (upserts if new)
    save_results(tenant_id, results)         -> int
    import_from_excel(tenant_id, path)       -> int
    company_numbers(tenant_id)               -> set[str]
    all_results(tenant_id)                   -> list[dict]

    operator-only:
    all_rows_all_tenants()                   -> list[dict] (+ "Tenant Id")
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import time

import psycopg2
import psycopg2.extras

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ── Column mapping ──────────────────────────────────────────────────────────
# (human header label, snake_case db column). Order defines display/export order.
COLUMNS: list[tuple[str, str]] = [
    ("No.",                     "row_no"),
    ("Company Number",          "company_number"),
    ("Company Name",            "company_name"),
    ("Short Name",              "short_name"),
    ("Status",                  "status"),
    ("Type",                    "type"),
    ("Date of Creation",        "date_of_creation"),
    ("SIC Codes",               "sic_codes"),
    ("Address",                 "address"),
    ("Directors",               "directors"),
    ("Director Nationalities",  "director_nationalities"),
    ("Director DOB",            "director_dob"),
    ("Director Gender",         "director_gender"),
    ("Buvei First Name",        "buvei_first_name"),
    ("Buvei Last Name",         "buvei_last_name"),
    ("DUNS Number",             "duns_number"),
    ("DUNS Status",             "duns_status"),
    ("DUNS Email Used",         "duns_email_used"),
    ("D&B Legal Name",          "dnb_legal_name"),
    ("D&B Address",             "dnb_address"),
    ("Certificate Downloaded",  "certificate_downloaded"),
    ("Certificate Path",        "certificate_path"),
    ("Domain",                  "domain"),
    ("Domain Status",           "domain_status"),
    ("Domain Cost",             "domain_cost"),
    ("Assigned Email",          "assigned_email"),
    ("Account Name",            "account_name"),
    ("Developer Email",         "developer_email"),
    ("Dev Email Forwarding",    "dev_email_forwarding"),
    ("Google TXT Status",       "google_txt_status"),
    ("Rep ID Status",           "rep_id_status"),
    ("Organization Phone",      "organization_phone"),
    ("Play Signup Missing",     "play_signup_missing"),
    ("Company Status",          "pipeline_status"),
    ("Company Notes",           "company_notes"),
    ("Archived",                "archived"),
]
HEADER_TO_COL = {h: c for h, c in COLUMNS}
COL_TO_HEADER = {c: h for h, c in COLUMNS}
TABLE = "companies"
TENANTS_TABLE = "tenants"
USERS_TABLE = "users"
# Representative ID details a client supplies for Play Console signup: their
# own name, personal address and ID document references. Personal data — keep
# it tenant-scoped, out of logs, and out of any cross-tenant view.
MANUAL_TABLE = "company_manual_inputs"

VALID_ROLES = ("client", "operator")

# Tenant every pre-tenancy row is migrated into.
DEFAULT_TENANT_SLUG = "default"


# ── Connection ──────────────────────────────────────────────────────────────
def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL not set. Add it to .env "
            "(postgresql://...pooler.supabase.com:5432/postgres)."
        )
    return dsn


def _connect():
    return psycopg2.connect(_dsn(), connect_timeout=15,
                            cursor_factory=psycopg2.extras.RealDictCursor)


# ── Value normalisation ─────────────────────────────────────────────────────
def _cn8(v) -> str:
    s = str(v or "").strip()
    return s.zfill(8) if s.isdigit() else s


def _norm(value):
    """Coerce a Python value into what we store (text, or None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).strip()
    return s if s != "" else None


# ── Schema ──────────────────────────────────────────────────────────────────
def _companies_ddl() -> str:
    cols_sql = ["tenant_id INTEGER NOT NULL"]
    for header, col in COLUMNS:
        if col == "company_number":
            cols_sql.append(f"{col} TEXT NOT NULL")
        elif col == "row_no":
            cols_sql.append(f"{col} INTEGER")
        else:
            cols_sql.append(f"{col} TEXT")
    return (
        f"CREATE TABLE IF NOT EXISTS {TABLE} (\n  "
        + ",\n  ".join(cols_sql)
        + ",\n  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        + f",\n  PRIMARY KEY (tenant_id, company_number)\n);"
    )


def _pk_columns(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT a.attname
          FROM pg_index i
          JOIN pg_attribute a
            ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
         WHERE i.indrelid = %s::regclass AND i.indisprimary
         ORDER BY array_position(i.indkey, a.attnum)
        """,
        (table,),
    )
    return [r["attname"] for r in cur.fetchall()]


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS t", (table,))
    return cur.fetchone()["t"] is not None


def ensure_schema() -> None:
    """
    Create every table, and migrate a pre-tenancy `companies` table in place.

    Safe to run repeatedly. The migration path adds tenant_id, backfills it
    with the default tenant, and swaps the primary key from (company_number)
    to (tenant_id, company_number).
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TENANTS_TABLE} (
              id          SERIAL PRIMARY KEY,
              slug        TEXT NOT NULL UNIQUE,
              name        TEXT NOT NULL DEFAULT '',
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
              id             SERIAL PRIMARY KEY,
              tenant_id      INTEGER NOT NULL REFERENCES {TENANTS_TABLE}(id) ON DELETE CASCADE,
              email          TEXT NOT NULL UNIQUE,
              password_hash  TEXT NOT NULL,
              role           TEXT NOT NULL DEFAULT 'client',
              created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS users_tenant_idx ON {USERS_TABLE} (tenant_id)"
        )

        # Default tenant, so a pre-tenancy table has somewhere to land.
        cur.execute(
            f"INSERT INTO {TENANTS_TABLE} (slug, name) VALUES (%s, %s) "
            f"ON CONFLICT (slug) DO NOTHING",
            (DEFAULT_TENANT_SLUG, "Default"),
        )
        cur.execute(
            f"SELECT id FROM {TENANTS_TABLE} WHERE slug = %s", (DEFAULT_TENANT_SLUG,)
        )
        default_tenant = int(cur.fetchone()["id"])

        fresh = not _table_exists(cur, TABLE)
        cur.execute(_companies_ddl())

        if not fresh:
            # CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so
            # bring it up to the current COLUMNS list before anything selects
            # from it. Adding a column to COLUMNS is then all a change needs.
            for _header, col in COLUMNS:
                if col == "company_number":
                    continue  # part of the primary key; always present
                cur.execute(
                    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {col} "
                    f"{'INTEGER' if col == 'row_no' else 'TEXT'}"
                )

            # Pre-tenancy table: add the column, backfill, then swap the PK.
            cur.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS tenant_id INTEGER"
            )
            cur.execute(
                f"UPDATE {TABLE} SET tenant_id = %s WHERE tenant_id IS NULL",
                (default_tenant,),
            )
            cur.execute(f"ALTER TABLE {TABLE} ALTER COLUMN tenant_id SET NOT NULL")

            if _pk_columns(cur, TABLE) != ["tenant_id", "company_number"]:
                cur.execute(
                    f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {TABLE}_pkey"
                )
                cur.execute(
                    f"ALTER TABLE {TABLE} "
                    f"ADD CONSTRAINT {TABLE}_pkey PRIMARY KEY (tenant_id, company_number)"
                )

        cur.execute(
            f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {TABLE}_tenant_fk"
        )
        cur.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {TABLE}_tenant_fk "
            f"FOREIGN KEY (tenant_id) REFERENCES {TENANTS_TABLE}(id) ON DELETE CASCADE"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS companies_tenant_idx ON {TABLE} (tenant_id)"
        )

        # Manual inputs: one JSONB blob per company, so the shape can grow
        # without a migration. Tenant-scoped like everything else.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MANUAL_TABLE} (
              tenant_id       INTEGER NOT NULL REFERENCES {TENANTS_TABLE}(id) ON DELETE CASCADE,
              company_number  TEXT NOT NULL,
              data            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
              updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (tenant_id, company_number)
            );
            """
        )
        conn.commit()
    _invalidate()


# ── Read cache (short TTL; invalidated on writes) ───────────────────────────
# Keyed by tenant. A single shared entry would hand one tenant's rows to the
# next caller inside the TTL window, so the key is not optional.
_CACHE: dict[int, tuple[float, list[dict]]] = {}
_LOCK = threading.Lock()
_TTL = 1.5  # seconds


def _invalidate(tenant_id: int | None = None) -> None:
    """Drop cached rows for one tenant, or for all of them when None."""
    with _LOCK:
        if tenant_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(int(tenant_id), None)


def _row_to_headerdict(dbrow: dict) -> dict:
    out = {}
    for header, col in COLUMNS:
        if col == "row_no":
            out[header] = dbrow.get(col)
        else:
            out[header] = dbrow.get(col)
    return out


def _tid(tenant_id: int) -> int:
    """Coerce and sanity-check a tenant id. Refuses None so scoping can't be skipped."""
    if tenant_id is None:
        raise ValueError("tenant_id is required")
    value = int(tenant_id)
    if value <= 0:
        raise ValueError(f"invalid tenant_id: {tenant_id!r}")
    return value


# ── Current tenant (per request / per background job) ───────────────────────
# Set once at the edge — by the auth layer for a request, or from the job
# payload for a worker — then read by call sites that can't take the id
# directly. Unset means raise, never "all tenants": a missed call site must
# fail loudly rather than serve someone else's companies.
_current_tenant: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_tenant", default=None,
)


def set_current_tenant(tenant_id: int | None) -> None:
    _current_tenant.set(None if tenant_id is None else _tid(tenant_id))


@contextlib.contextmanager
def tenant_scope(tenant_id: int):
    """Run a block bound to one tenant, restoring the previous one after."""
    token = _current_tenant.set(_tid(tenant_id))
    try:
        yield
    finally:
        _current_tenant.reset(token)


def current_tenant_id() -> int:
    """
    The tenant bound to this request or job.

    Falls back to the tenant named by SINGLE_TENANT_SLUG when nothing is bound,
    which keeps a single-client deployment working before the auth layer lands.
    That fallback must be configured deliberately; without it, this raises.
    """
    bound = _current_tenant.get()
    if bound is not None:
        return bound

    slug = (os.getenv("SINGLE_TENANT_SLUG") or "").strip().lower()
    if slug:
        cached = _SINGLE_TENANT_CACHE.get(slug)
        if cached is None:
            tenant = get_tenant_by_slug(slug)
            if tenant is None:
                raise RuntimeError(
                    f"SINGLE_TENANT_SLUG={slug!r} but no such tenant exists. "
                    f"Create it with: python db.py add-tenant {slug}"
                )
            cached = int(tenant["id"])
            _SINGLE_TENANT_CACHE[slug] = cached
        return cached

    raise RuntimeError(
        "No tenant bound to this request or job. Call db.set_current_tenant() "
        "at the edge, use db.tenant_scope(...), or set SINGLE_TENANT_SLUG for a "
        "single-client deployment."
    )


_SINGLE_TENANT_CACHE: dict[str, int] = {}


def all_rows(tenant_id: int, use_cache: bool = True) -> list[dict]:
    """Every company belonging to `tenant_id`, keyed by header label."""
    tid = _tid(tenant_id)
    if use_cache:
        with _LOCK:
            hit = _CACHE.get(tid)
            if hit is not None and (time.time() - hit[0]) < _TTL:
                return hit[1]
    cols = ", ".join(col for _, col in COLUMNS)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {cols} FROM {TABLE} WHERE tenant_id = %s "
            f"ORDER BY row_no NULLS LAST, company_number",
            (tid,),
        )
        rows = [_row_to_headerdict(r) for r in cur.fetchall()]
    with _LOCK:
        _CACHE[tid] = (time.time(), rows)
    return rows


def all_rows_all_tenants() -> list[dict]:
    """
    Every company across every tenant, each row carrying "Tenant Id".

    Operator/admin views only — never reachable from a client-scoped request.
    Deliberately uncached.
    """
    cols = ", ".join(col for _, col in COLUMNS)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT tenant_id, {cols} FROM {TABLE} "
            f"ORDER BY tenant_id, row_no NULLS LAST, company_number"
        )
        out = []
        for r in cur.fetchall():
            row = _row_to_headerdict(r)
            row["Tenant Id"] = r.get("tenant_id")
            out.append(row)
    return out


def find_row(tenant_id: int, cn: str) -> dict | None:
    """One company within a tenant. Queries directly rather than scanning."""
    tid = _tid(tenant_id)
    target = _cn8(cn)
    if not target:
        return None
    cols = ", ".join(col for _, col in COLUMNS)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {cols} FROM {TABLE} WHERE tenant_id = %s AND company_number = %s",
            (tid, target),
        )
        row = cur.fetchone()
    return _row_to_headerdict(row) if row else None


def company_numbers(tenant_id: int) -> set[str]:
    tid = _tid(tenant_id)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT company_number FROM {TABLE} WHERE tenant_id = %s", (tid,)
        )
        return {_cn8(r["company_number"]) for r in cur.fetchall() if r["company_number"]}


def _next_row_no(cur, tenant_id: int) -> int:
    """Row numbers run 1..n within each tenant, not globally."""
    cur.execute(
        f"SELECT COALESCE(MAX(row_no), 0) AS m FROM {TABLE} WHERE tenant_id = %s",
        (tenant_id,),
    )
    return int(cur.fetchone()["m"]) + 1


# ── Writes ──────────────────────────────────────────────────────────────────
def add_company(tenant_id: int, cn: str, name: str = "", date_of_creation: str = "") -> bool:
    """
    Insert a company for this tenant. Returns True if inserted, False if the
    tenant already holds it. Another tenant holding the same company is not a
    conflict.
    """
    tid = _tid(tenant_id)
    cn = _cn8(cn)
    if not cn or cn == "00000000":
        return False
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE tenant_id = %s AND company_number = %s",
            (tid, cn),
        )
        if cur.fetchone():
            return False
        rn = _next_row_no(cur, tid)
        cur.execute(
            f"INSERT INTO {TABLE} (tenant_id, company_number, company_name, date_of_creation, row_no) "
            f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, company_number) DO NOTHING",
            (tid, cn, _norm(name), _norm(date_of_creation), rn),
        )
        conn.commit()
    _invalidate(tid)
    return True


def update_row(tenant_id: int, cn: str, updates: dict) -> None:
    """Update columns for one of this tenant's companies. Upserts if new."""
    tid = _tid(tenant_id)
    cn = _cn8(cn)
    if not cn:
        return
    set_cols = {}
    for header, value in updates.items():
        col = HEADER_TO_COL.get(header)
        if col is None or col in ("company_number", "tenant_id"):
            continue
        set_cols[col] = _norm(value)
    if not set_cols:
        return
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE tenant_id = %s AND company_number = %s",
            (tid, cn),
        )
        exists = cur.fetchone() is not None
        if not exists:
            rn = _next_row_no(cur, tid)
            set_cols.setdefault("row_no", rn)
            cols = ["tenant_id", "company_number"] + list(set_cols.keys())
            vals = [tid, cn] + list(set_cols.values())
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        else:
            assignments = ", ".join(f"{c} = %s" for c in set_cols)
            cur.execute(
                f"UPDATE {TABLE} SET {assignments}, updated_at = now() "
                f"WHERE tenant_id = %s AND company_number = %s",
                list(set_cols.values()) + [tid, cn],
            )
        conn.commit()
    _invalidate(tid)


# Map a nested pipeline result dict (as produced by run_pipeline) to header labels.
def _result_to_headers(r: dict) -> dict:
    d   = r.get("details", {}) or {}
    dn  = r.get("duns", {}) or {}
    ce  = r.get("certificate", {}) or {}
    dm  = r.get("domain", {}) or {}
    em  = r.get("email", {}) or {}
    pc  = r.get("play_console", {}) or {}
    ph  = r.get("phone", {}) or {}
    cert_ok = (ce.get("status") == "downloaded") or bool(ce.get("path"))
    return {
        "Company Number":         r.get("company_number", ""),
        "Company Name":           d.get("company_name", ""),
        "Status":                 d.get("company_status", ""),
        "Type":                   d.get("company_type", ""),
        "Date of Creation":       d.get("date_of_creation", ""),
        "SIC Codes":              d.get("sic_codes", ""),
        "Address":                d.get("address", ""),
        "Directors":              d.get("director_names", ""),
        "Director Nationalities": d.get("director_nationalities", ""),
        "DUNS Number":            dn.get("duns_number", ""),
        "DUNS Status":            dn.get("status", ""),
        "DUNS Email Used":        dn.get("temp_email", ""),
        "D&B Legal Name":         dn.get("dnb_name", ""),
        "D&B Address":            dn.get("dnb_address", ""),
        "Certificate Downloaded": "Yes" if cert_ok else "",
        "Certificate Path":       ce.get("path", "") or ce.get("error", ""),
        "Domain":                 dm.get("domain", ""),
        "Domain Status":          dm.get("status", ""),
        "Domain Cost":            dm.get("charged", ""),
        "Assigned Email":         em.get("email", ""),
        "Account Name":           em.get("account_name", ""),
        "Developer Email":        pc.get("developer_email", ""),
        "Dev Email Forwarding":   (pc.get("developer_email_forwarding", {}) or {}).get("status", ""),
        "Google TXT Status":      (pc.get("google_txt", {}) or {}).get("status", ""),
        "Organization Phone":     ph.get("phone_number", ""),
    }


def _headers_to_result(row: dict) -> dict:
    """Inverse of _result_to_headers: header-keyed row -> nested pipeline result."""
    g = lambda h: row.get(h) or ""
    cert_dl = str(g("Certificate Downloaded")).strip().lower() in ("yes", "true", "1")
    return {
        "company_number": str(g("Company Number")),
        "details": {
            "company_name":           g("Company Name"),
            "company_status":         g("Status"),
            "company_type":           g("Type"),
            "date_of_creation":       g("Date of Creation"),
            "sic_codes":              g("SIC Codes"),
            "address":                g("Address"),
            "director_names":         g("Directors"),
            "director_nationalities": g("Director Nationalities"),
        },
        "duns": {
            "duns_number": g("DUNS Number"),
            "status":      g("DUNS Status"),
            "temp_email":  g("DUNS Email Used"),
            "dnb_name":    g("D&B Legal Name"),
            "dnb_address": g("D&B Address"),
        },
        "certificate": {
            "path":   g("Certificate Path") if cert_dl else "",
            "status": "downloaded" if cert_dl else "",
            "error":  g("Certificate Path") if not cert_dl else "",
        },
        "domain":  {"domain": g("Domain"), "status": g("Domain Status"), "charged": g("Domain Cost")},
        "email":   {"email": g("Assigned Email"), "account_name": g("Account Name")},
        "play_console": {
            "developer_email": g("Developer Email"),
            "developer_email_forwarding": {"status": g("Dev Email Forwarding")},
            "google_txt": {"status": g("Google TXT Status")},
        },
        "phone": {"phone_number": g("Organization Phone")},
    }


def all_results(tenant_id: int) -> list[dict]:
    """This tenant's companies as nested pipeline-result dicts (run_pipeline)."""
    return [
        _headers_to_result(r)
        for r in all_rows(tenant_id, use_cache=False)
        if r.get("Company Number")
    ]


def save_results(tenant_id: int, results: list[dict]) -> int:
    """Upsert nested pipeline result dicts for one tenant. Returns count processed."""
    tid = _tid(tenant_id)
    n = 0
    for r in results:
        cn = _cn8(r.get("company_number", ""))
        if not cn or cn == "00000000":
            continue
        headers = {k: v for k, v in _result_to_headers(r).items() if v not in (None, "")}
        headers.pop("Company Number", None)
        update_row(tid, cn, headers)
        n += 1
    return n


# ── One-time migration from the old Excel file ──────────────────────────────
def unmapped_excel_values(path: str) -> list[dict]:
    """
    Report populated cells the import would NOT carry over.

    A workbook edited by hand can grow a column whose header was renamed or
    deleted. Those cells map to nothing and would vanish silently, so list
    them instead and let a human decide where they belong.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    hdr_row = next(it, None)
    if not hdr_row:
        wb.close()
        return []
    hdr = [str(v or "").strip() for v in hdr_row]
    known = {h for h, _ in COLUMNS}
    orphan_cols = [i for i, h in enumerate(hdr) if h not in known]

    found: list[dict] = []
    for n, row in enumerate(it, start=2):
        if not row:
            continue
        for i in orphan_cols:
            if i < len(row) and row[i] not in (None, ""):
                found.append({
                    "row": n,
                    "company_number": _cn8(row[1]) if len(row) > 1 else "",
                    "column": hdr[i] or f"<no header, index {i}>",
                    "value": row[i],
                })
    wb.close()
    return found


def import_from_excel(tenant_id: int, path: str) -> int:
    import openpyxl
    tid = _tid(tenant_id)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    hdr_row = next(it, None)
    if not hdr_row:
        wb.close()
        return 0
    hdr = [str(v or "").strip() for v in hdr_row]
    imported = 0
    with _connect() as conn, conn.cursor() as cur:
        for row in it:
            if not row or not row[1 if len(row) > 1 else 0]:
                continue
            rec = {hdr[i]: (row[i] if i < len(row) else None) for i in range(len(hdr))}
            cn = _cn8(rec.get("Company Number"))
            if not cn or cn == "00000000":
                continue
            set_cols = {"tenant_id": tid, "company_number": cn}
            for header, col in COLUMNS:
                if col == "company_number":
                    continue
                if header in rec:
                    set_cols[col] = _norm(rec.get(header))
            cols = list(set_cols.keys())
            vals = list(set_cols.values())
            placeholders = ", ".join(["%s"] * len(cols))
            updates = ", ".join(
                f"{c} = EXCLUDED.{c}" for c in cols
                if c not in ("company_number", "tenant_id")
            )
            cur.execute(
                f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT (tenant_id, company_number) DO UPDATE SET {updates}",
                vals,
            )
            imported += 1
        conn.commit()
    wb.close()
    _invalidate(tid)
    return imported


# ── Manual inputs (client-supplied representative ID details) ───────────────
def get_manual_inputs(tenant_id: int, cn: str) -> dict:
    """This tenant's stored manual inputs for one company ({} when none)."""
    tid = _tid(tenant_id)
    target = _cn8(cn)
    if not target:
        return {}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT data FROM {MANUAL_TABLE} WHERE tenant_id = %s AND company_number = %s",
            (tid, target),
        )
        row = cur.fetchone()
    return (row["data"] or {}) if row else {}


def save_manual_inputs(tenant_id: int, cn: str, data: dict) -> None:
    """
    Replace the manual inputs for one of this tenant's companies.

    A single upsert, so two clients saving at once cannot lose each other's
    work the way a read-modify-write of one shared JSON file did.
    """
    from psycopg2.extras import Json

    tid = _tid(tenant_id)
    target = _cn8(cn)
    if not target:
        return
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {MANUAL_TABLE} (tenant_id, company_number, data) "
            f"VALUES (%s, %s, %s) "
            f"ON CONFLICT (tenant_id, company_number) "
            f"DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (tid, target, Json(data or {})),
        )
        conn.commit()


def all_manual_inputs(tenant_id: int) -> dict[str, dict]:
    """Every manual-input record for this tenant, keyed by company number."""
    tid = _tid(tenant_id)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT company_number, data FROM {MANUAL_TABLE} WHERE tenant_id = %s",
            (tid,),
        )
        return {r["company_number"]: (r["data"] or {}) for r in cur.fetchall()}


def import_manual_inputs_json(tenant_id: int, path: str) -> int:
    """One-time migration of the old global manual_inputs.json into a tenant."""
    import json as _json

    tid = _tid(tenant_id)
    with open(path, "r", encoding="utf-8") as fh:
        payload = _json.load(fh)
    n = 0
    for cn, data in (payload or {}).items():
        if _cn8(cn):
            save_manual_inputs(tid, cn, data)
            n += 1
    return n


# ── Tenants ─────────────────────────────────────────────────────────────────
def create_tenant(slug: str, name: str = "") -> int:
    """Create a tenant (or return the existing one with this slug)."""
    slug = (slug or "").strip().lower()
    if not slug:
        raise ValueError("tenant slug is required")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TENANTS_TABLE} (slug, name) VALUES (%s, %s) "
            f"ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (slug, name or slug),
        )
        tid = int(cur.fetchone()["id"])
        conn.commit()
    return tid


def get_tenant(tenant_id: int) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {TENANTS_TABLE} WHERE id = %s", (_tid(tenant_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_tenant_by_slug(slug: str) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {TENANTS_TABLE} WHERE slug = %s", ((slug or "").strip().lower(),)
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_tenants() -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {TENANTS_TABLE} ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


# ── Users ───────────────────────────────────────────────────────────────────
def _hash(password: str) -> str:
    # werkzeug ships with Flask; no extra dependency, and no hand-rolled crypto.
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


def create_user(tenant_id: int, email: str, password: str, role: str = "client") -> int:
    """Create a login belonging to a tenant."""
    tid = _tid(tenant_id)
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")
    if not password:
        raise ValueError("password is required")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {USERS_TABLE} (tenant_id, email, password_hash, role) "
            f"VALUES (%s, %s, %s, %s) RETURNING id",
            (tid, email, _hash(password), role),
        )
        uid = int(cur.fetchone()["id"])
        conn.commit()
    return uid


def get_user(user_id: int) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, tenant_id, email, role, created_at FROM {USERS_TABLE} WHERE id = %s",
            (int(user_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def verify_user(email: str, password: str) -> dict | None:
    """Return the user dict (with tenant_id) on a correct password, else None."""
    from werkzeug.security import check_password_hash

    email = (email or "").strip().lower()
    if not email or not password:
        return None
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, tenant_id, email, role, password_hash FROM {USERS_TABLE} "
            f"WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    user = dict(row)
    user.pop("password_hash", None)
    return user


def set_password(user_id: int, password: str) -> None:
    if not password:
        raise ValueError("password is required")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {USERS_TABLE} SET password_hash = %s WHERE id = %s",
            (_hash(password), int(user_id)),
        )
        conn.commit()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Schema and tenant admin.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("migrate", help="create/migrate all tables (default)")

    p_tenant = sub.add_parser("add-tenant", help="create a tenant")
    p_tenant.add_argument("slug")
    p_tenant.add_argument("--name", default="")

    p_user = sub.add_parser("add-user", help="create a login for a tenant")
    p_user.add_argument("tenant_slug")
    p_user.add_argument("email")
    p_user.add_argument("password")
    p_user.add_argument("--role", default="client", choices=VALID_ROLES)

    sub.add_parser("list-tenants", help="list tenants and their company counts")

    p_imp = sub.add_parser("import", help="import an Excel workbook into a tenant")
    p_imp.add_argument("tenant_slug")
    p_imp.add_argument("path", nargs="?", default="pipeline_output/companies_pipeline.xlsx")

    p_mi = sub.add_parser("import-manual-inputs",
                          help="import the old manual_inputs.json into a tenant")
    p_mi.add_argument("tenant_slug")
    p_mi.add_argument("path", nargs="?", default="pipeline_output/manual_inputs.json")

    p_chk = sub.add_parser("check-excel",
                           help="list workbook cells an import would not carry over")
    p_chk.add_argument("path", nargs="?", default="pipeline_output/companies_pipeline.xlsx")

    args = parser.parse_args()
    ensure_schema()

    if args.cmd == "add-tenant":
        print(f"tenant {args.slug} -> id {create_tenant(args.slug, args.name)}")
    elif args.cmd == "add-user":
        t = get_tenant_by_slug(args.tenant_slug)
        if not t:
            raise SystemExit(f"No such tenant: {args.tenant_slug}")
        uid = create_user(t["id"], args.email, args.password, args.role)
        print(f"user {args.email} ({args.role}) -> id {uid}, tenant {t['slug']}")
    elif args.cmd == "import":
        t = get_tenant_by_slug(args.tenant_slug)
        if not t:
            raise SystemExit(f"No such tenant: {args.tenant_slug}")
        orphans = unmapped_excel_values(args.path)
        print(f"Imported {import_from_excel(t['id'], args.path)} rows into {t['slug']}.")
        if orphans:
            print(f"\n{len(orphans)} cell(s) were NOT imported (no matching column):")
            for o in orphans:
                print(f"  row {o['row']} company {o['company_number']}: "
                      f"{o['column']} = {o['value']!r}")
            print("The workbook still holds them; place them by hand if they matter.")
    elif args.cmd == "import-manual-inputs":
        t = get_tenant_by_slug(args.tenant_slug)
        if not t:
            raise SystemExit(f"No such tenant: {args.tenant_slug}")
        n = import_manual_inputs_json(t["id"], args.path)
        print(f"Imported manual inputs for {n} company/companies into {t['slug']}.")
    elif args.cmd == "check-excel":
        orphans = unmapped_excel_values(args.path)
        if not orphans:
            print("Every populated cell maps to a known column.")
        else:
            print(f"{len(orphans)} cell(s) would NOT be imported:")
            for o in orphans:
                print(f"  row {o['row']} company {o['company_number']}: "
                      f"{o['column']} = {o['value']!r}")
    elif args.cmd == "list-tenants":
        for t in list_tenants():
            print(f"{t['id']:>4}  {t['slug']:<24} {len(all_rows(t['id'], use_cache=False)):>5} companies")
    else:
        print(f"Schema ready. Table '{TABLE}' has {len(COLUMNS)} mapped columns.")
        for t in list_tenants():
            print(f"  tenant {t['id']}: {t['slug']}")
