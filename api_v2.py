"""
Per-company stage control API — v2.
Register with:  from api_v2 import api_v2; app.register_blueprint(api_v2)
"""
from __future__ import annotations

import io, json, os, re, shutil, socket, sys, threading, time, traceback, uuid
from datetime import datetime
from pathlib import Path

import openpyxl
from flask import Blueprint, jsonify, request, send_file, abort

import db  # Supabase (PostgreSQL) data-access layer — source of truth
import company_paths  # one definition of where a company's files live
from auth import operator_required  # second line behind auth's before_request gate

api_v2 = Blueprint("api_v2", __name__)

ROOT     = Path(__file__).parent
EXCEL    = ROOT / "pipeline_output" / "companies_pipeline.xlsx"
DL_BASE  = ROOT / "pipeline_output"          # files must be under here to download

# Ensure the companies table exists on startup (idempotent).
try:
    db.ensure_schema()
except Exception as _e:
    print(f"  [db] ensure_schema at import failed: {_e}")


def reclaim_interrupted_jobs() -> int:
    """
    Fail jobs left behind by a process that is gone. Call once, at startup.

    A row says "running" because a thread in some process said so. If that
    process died — deploy, OOM, kill — nothing will ever finish the row, and
    every later attempt to run that stage is refused as "already running".
    This is the reason the registry had to be durable rather than in memory:
    an in-memory one forgot the job entirely, which hid the interruption from
    the client instead of reporting it.
    """
    try:
        reclaimed = db.reclaim_stale_jobs(WORKER_ID)
        if reclaimed:
            print(f"  [jobs] marked {reclaimed} interrupted job(s) failed after restart")
        return reclaimed
    except Exception as e:  # noqa: BLE001 - never block startup on this
        print(f"  [jobs] could not reclaim interrupted jobs: {e}")
        return 0


STAGE_ORDER = ["details", "duns", "certificate", "domain", "email", "director_id", "app", "publish"]

# UI field key -> spreadsheet/DB header label. Whitelist of manually-editable fields.
# Editing any of these overrides the value the pipeline would otherwise produce;
# because stage completion + the info panels are all derived from the company row,
# a manual edit propagates across the whole interface on the next fetch.
EDITABLE_FIELDS = {
    "name":                 "Company Name",
    "status":               "Status",
    "type":                 "Type",
    "date_of_creation":     "Date of Creation",
    "sic":                  "SIC Codes",
    "address":              "Address",
    "directors":            "Directors",
    "nationalities":        "Director Nationalities",
    "dobs":                 "Director DOB",
    "genders":              "Director Gender",
    "buvei_first":          "Buvei First Name",
    "buvei_last":           "Buvei Last Name",
    "duns":                 "DUNS Number",
    "duns_status":          "DUNS Status",
    "duns_email":           "DUNS Email Used",
    "dnb_name":             "D&B Legal Name",
    "dnb_address":          "D&B Address",
    "domain":               "Domain",
    "domain_status":        "Domain Status",
    "domain_cost":          "Domain Cost",
    "email":                "Assigned Email",
    "account_name":         "Account Name",
    "developer_email":      "Developer Email",
    "dev_email_forwarding": "Dev Email Forwarding",
    "google_txt_status":    "Google TXT Status",
    "organization_phone":   "Organization Phone",
    "certificate_path":     "Certificate Path",
    "certificate_downloaded": "Certificate Downloaded",
}

# ── Job state ──────────────────────────────────────────────────────────────
# Job rows live in Postgres (db.job_*). Only the live log tail is in memory:
# it is flushed to the row every couple of seconds, and reading it from
# memory while a stage runs keeps the dashboard's 2.5s poll off the database
# for output that is about to be overwritten anyway.
_LOCK = threading.Lock()
_CO_LOCKS: dict[str, threading.Lock] = {}   # per-company serialisation


def _co_lock(cn: str) -> threading.Lock:
    with _LOCK:
        if cn not in _CO_LOCKS:
            _CO_LOCKS[cn] = threading.Lock()
        return _CO_LOCKS[cn]


def _tenant() -> int:
    """The tenant bound to the current request."""
    return db.current_tenant_id()


#: Identifies this server process in the jobs table. A job row claimed by a
#: different id belongs to a process that is no longer here.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _jk(cn: str, stage: str, tenant_id: int | None = None) -> str:
    """Key for the in-memory live-log buffer. Tenant-scoped like the rows."""
    tid = _tenant() if tenant_id is None else tenant_id
    return f"{tid}:{cn}:{stage}"


def _job_get(cn: str, stage: str, tenant_id: int | None = None) -> dict:
    """
    One stage's latest job.

    The persisted log is flushed every couple of seconds, so while a stage is
    running the in-memory buffer is ahead of the row; prefer it, and fall back
    to the row once the thread is gone.
    """
    tid = _tenant() if tenant_id is None else tenant_id
    job = db.job_get(tid, cn, stage) or {}
    if job.get("status") in db.ACTIVE_JOB_STATUSES:
        live = _TEE.get_log(_jk(cn, stage, tid))
        if live:
            job["log"] = live
    return job


def _job_set(cn: str, stage: str, tenant_id: int | None = None, **kw) -> None:
    tid = _tenant() if tenant_id is None else tenant_id
    db.job_update(tid, cn, stage, **kw)


# ── Stdout capture ─────────────────────────────────────────────────────────
# Single global _TEE is installed at sys.stdout once. Each worker thread calls
# _TEE.bind(key) so its prints are routed to its own buffer; no global
# sys.stdout replacement, no race condition between concurrent jobs.

_TEE_LOCK = threading.Lock()


class _Tee:
    """Global stdout multiplexer — installed once at module load."""

    def __init__(self):
        self._real = sys.__stdout__
        self._local = threading.local()
        self._logs: dict[str, list[str]] = {}  # key → chunks

    def bind(self, key: str) -> None:
        self._local.key = key
        with _TEE_LOCK:
            self._logs[key] = []

    def get_log(self, key: str) -> str:
        with _TEE_LOCK:
            return "".join(self._logs.get(key, []))

    def unbind(self) -> str:
        key = getattr(self._local, "key", None)
        log = ""
        if key:
            with _TEE_LOCK:
                log = "".join(self._logs.pop(key, []))
            self._local.key = None
        return log

    def write(self, s: str) -> int:
        if s:
            try: self._real.write(s); self._real.flush()
            except Exception: pass
            key = getattr(self._local, "key", None)
            if key:
                with _TEE_LOCK:
                    if key in self._logs:
                        self._logs[key].append(s)
        return len(s) if s else 0

    def flush(self):
        try: self._real.flush()
        except Exception: pass


_TEE = _Tee()
sys.stdout = _TEE


# The Android build is the one stage that can exhaust a shared box: Gradle
# already uses every core, so running several at once makes all of them slower
# and risks the OOM killer. Other stages are network-bound and can overlap.
_BUILD_SLOTS = threading.Semaphore(max(1, int(os.getenv("MAX_CONCURRENT_BUILDS", "1"))))
_HEAVY_STAGES = {"app"}




#: How often a running job writes its log and heartbeat back to the row.
#: Short enough that a crash loses little, long enough not to hammer the
#: database for a build that prints for minutes.
_FLUSH_SECONDS = 2.0

#: Longest log kept per job. A Gradle build prints a lot, and the row is read
#: on every poll; past this the head is dropped, which is the part a reader
#: has already seen.
_LOG_LIMIT = 200_000


def _trim(log: str) -> str:
    if len(log) <= _LOG_LIMIT:
        return log
    return "…earlier output trimmed…\n" + log[-_LOG_LIMIT:]


def _heartbeat(tenant_id: int, cn: str, stage: str, key: str, stop: threading.Event) -> None:
    """Flush the live log and touch the heartbeat until the stage finishes."""
    while not stop.wait(_FLUSH_SECONDS):
        try:
            db.job_update(tenant_id, cn, stage,
                          log=_trim(_TEE.get_log(key)),
                          heartbeat_at=datetime.now())
        except Exception as e:  # noqa: BLE001 - never kill the stage over a flush
            print(f"  [jobs] heartbeat failed for {cn}/{stage}: {e}")


def _run_tracked(tenant_id: int, cn: str, stage: str, fn) -> None:
    """
    Run one stage, in this thread, recording its state and output.

    The tenant has to be rebound here. Flask binds it per request in a
    ContextVar, and a new thread starts with an empty context — so without
    this the job either raises, or silently falls back to SINGLE_TENANT_SLUG
    and writes another client's row.

    The caller has already claimed the job row, so this owns it.
    """
    key = _jk(cn, stage, tenant_id)
    heavy = stage in _HEAVY_STAGES

    if heavy:
        # Claimed as "queued"; promote to "running" once a slot frees.
        _BUILD_SLOTS.acquire()
        db.job_update(tenant_id, cn, stage, status="running",
                      started_at=datetime.now(), heartbeat_at=datetime.now())

    _TEE.bind(key)
    stop = threading.Event()
    beat = threading.Thread(
        target=_heartbeat, args=(tenant_id, cn, stage, key, stop), daemon=True,
    )
    beat.start()
    try:
        with db.tenant_scope(tenant_id):
            result = fn()
        stop.set()
        log = _trim(_TEE.unbind())
        db.job_update(tenant_id, cn, stage, status="done",
                      finished_at=datetime.now(), log=log,
                      result=str(result)[:500])
    except Exception as e:
        stop.set()
        log = _trim(_TEE.unbind() + "\n" + traceback.format_exc())
        db.job_update(tenant_id, cn, stage, status="failed",
                      finished_at=datetime.now(), error=str(e), log=log)
        raise
    finally:
        stop.set()
        if heavy:
            _BUILD_SLOTS.release()


def _spawn(cn: str, stage: str, fn) -> bool:
    """
    Claim a stage and run it in a daemon thread. False if already active.

    The claim is what serialises concurrent starts: it is one statement, so
    two requests racing on the same stage cannot both win.
    """
    tenant_id = _tenant()   # read here: the worker thread will not inherit it
    initial = "queued" if stage in _HEAVY_STAGES else "running"
    if not db.job_claim(tenant_id, cn, stage, WORKER_ID, status=initial):
        return False

    def _worker():
        try:
            _run_tracked(tenant_id, cn, stage, fn)
        except Exception:
            pass  # already recorded on the job

    threading.Thread(target=_worker, daemon=True).start()
    return True


# ── Excel helpers ──────────────────────────────────────────────────────────

def _cn8(v) -> str:
    return str(v or "").strip().zfill(8)


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def _co_dir(cn: str, name: str = "") -> Path:
    """
    This tenant's directory for a company.

    Tenant-scoped via company_paths: the old layout keyed the directory on
    the number and name alone, so a leftover directory — from an earlier
    run, a restored backup, or another tenant holding the same company
    number — made a brand-new company report its stages already done.
    """
    return company_paths.company_dir(cn, name)


_ROWS_CACHE = None  # retained for backwards-compat; caching now lives in db.py


def _all_rows() -> list[dict]:
    """All company rows from Supabase, keyed by the original header labels."""
    try:
        return db.all_rows(db.current_tenant_id())
    except Exception as e:
        print(f"  [db] all_rows failed: {e}")
        return []


def _find_row(cn: str) -> dict | None:
    try:
        return db.find_row(db.current_tenant_id(), cn)
    except Exception as e:
        print(f"  [db] find_row failed: {e}")
        return None


def _update_excel_row(cn: str, updates: dict) -> None:
    """Update a company's columns in Supabase (name kept for call-site compat)."""
    try:
        db.update_row(db.current_tenant_id(), cn, updates)
    except Exception as e:
        print(f"  [db] update_row failed: {e}")


def _add_company_row(cn: str, name: str, date_of_creation: str = "") -> None:
    """Insert a new company into Supabase if it isn't there yet."""
    try:
        db.add_company(db.current_tenant_id(), cn, name, date_of_creation)
    except Exception as e:
        print(f"  [db] add_company failed: {e}")


# ── Stage status ───────────────────────────────────────────────────────────

def _is_published(cn: str) -> bool:
    """Whether this company has a committed Play release. Never raises."""
    try:
        import play_publisher
        return play_publisher.is_published(cn)
    except Exception as e:  # noqa: BLE001 - a missing receipt must not break the list
        print(f"  [publish] receipt check failed for {cn}: {e}")
        return False


def _publish_summary(cn: str) -> dict | None:
    """The last publish attempt, trimmed to what the dashboard renders."""
    try:
        import play_publisher
        rec = play_publisher.read_receipt(cn)
    except Exception:  # noqa: BLE001 - the panel is not worth an error page
        return None
    if not rec:
        return None
    return {
        "committed": bool(rec.get("committed")),
        "ok": bool(rec.get("ok")),
        "track": rec.get("track") or "",
        "version_code": rec.get("version_code"),
        "package_name": rec.get("package_name") or "",
        "at": rec.get("at") or "",
        "errors": (rec.get("errors") or [])[:3],
    }


def _compute_stages(cn: str, row: dict) -> dict:
    name  = str(row.get("Company Name") or "")
    cdir  = _co_dir(cn, name)

    has_cert = bool(row.get("Certificate Downloaded"))
    if not has_cert:
        # The fallback looked for "*_cert.pdf", a name from an older tree that
        # nothing in this one has ever written — the downloader saves
        # Certificate_{cn}.pdf. So a company whose row field was cleared could
        # never be recognised again with the PDF sitting right there. Match on
        # a real PDF of a plausible size instead of on a naming convention.
        cd = cdir / "certificate"
        has_cert = cd.is_dir() and any(
            f.lower().endswith(".pdf") and (cd / f).stat().st_size >= 1024
            for f in os.listdir(cd)
        )

    ad = cdir / "app" / "artifacts"
    has_app = ad.is_dir() and any(f.endswith((".apk", ".aab")) for f in os.listdir(ad))

    # "details" means Companies House has actually been read, not that
    # somebody typed a name. A manually added company starts with a number, a
    # name and sometimes a year — and used to show the stage as Done with an
    # empty address, no SIC codes and no directors. Status and address come
    # only from the lookup (or a deliberate manual override, which is the
    # same claim made by a person).
    fetched = bool(str(row.get("Status") or "").strip()) or bool(str(row.get("Address") or "").strip())

    return {
        "details":     bool(name) and fetched,
        "duns":        bool(row.get("DUNS Number")),
        "certificate": has_cert,
        "domain":      bool(row.get("Domain")),
        "email":       bool(row.get("Assigned Email")),
        "director_id": (cdir / "director_id" / "ID Front.jpeg").exists(),
        "app":         has_app,
        # Committed only. A dry run leaves a receipt too — that is the point,
        # it records that the edit validated — but it is not a release.
        "publish":     _is_published(cn),
    }


# ── File helpers ───────────────────────────────────────────────────────────

def _ls(d: Path, exts: list | None = None, names: list | None = None) -> list[dict]:
    if not d.is_dir(): return []
    out = []
    for f in sorted(d.rglob("*")):
        if not f.is_file(): continue
        rel = f.relative_to(d).as_posix()
        if exts and not any(rel.lower().endswith(e) for e in exts): continue
        if names and f.name not in names: continue
        out.append({"name": rel, "size": f.stat().st_size, "path": str(f)})
    return out


def _pp(cdir: Path) -> dict | None:
    for p in [cdir / "payment_profile" / "payment_profile.txt", cdir / "payment_profile.txt"]:
        if p.exists():
            return {"name": "payment_profile.txt", "size": p.stat().st_size, "path": str(p)}
    return None


def _version_file(cn: str, name: str) -> Path:
    return _co_dir(cn, name) / "app" / "version.json"


def _read_version(cn: str, name: str) -> dict:
    vf = _version_file(cn, name)
    if vf.exists():
        try:
            return json.loads(vf.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version_code": 1, "version_name": "1.0"}


def _write_version(cn: str, name: str, version_code: int, version_name: str) -> None:
    vf = _version_file(cn, name)
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(
        json.dumps({"version_code": version_code, "version_name": version_name}, indent=2),
        encoding="utf-8",
    )


def _read_manifest(cn: str, name: str) -> dict:
    """Read the app's manifest.json; return {} if not found."""
    f = _co_dir(cn, name) / "app" / "manifest.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _pc_file(cn: str, name: str) -> Path:
    return _co_dir(cn, name) / "app" / "play_console.json"


def _read_pc(cn: str, name: str, application_id: str = "", manifest: dict | None = None,
             assigned_email: str = "", domain: str = "") -> dict:
    """Return saved Play Console data, pre-filled with low-review-risk defaults."""
    if manifest is None:
        manifest = {}

    role_noun        = str(manifest.get("role_noun") or "staff")
    role_verb_start  = str(manifest.get("role_verb_start") or "start")
    role_verb_end    = str(manifest.get("role_verb_end") or "end")
    export_title     = str(manifest.get("export_title") or "Work Log")
    display_name     = str(manifest.get("display_name") or name)
    support_email    = assigned_email or str(manifest.get("support_email") or "")
    domain_val       = domain or str(manifest.get("domain") or "")

    short_desc = f"{display_name[:22]} — {role_noun.capitalize()} shift & hours tracker"[:80]

    full_desc = (
        f"Manage {role_noun} schedules and track working hours with ease.\n\n"
        f"{display_name} provides a simple, reliable solution for UK businesses to "
        f"record {role_noun} shifts, monitor attendance, and export time logs for payroll.\n\n"
        f"Key features:\n"
        f"• Log shift {role_verb_start} and {role_verb_end} times instantly\n"
        f"• View weekly schedule at a glance\n"
        f"• Export {export_title} reports\n"
        f"• Secure and private — data stays on your device\n"
        f"• Designed for UK Working Time Regulations compliance\n\n"
        f"Simple, fast, and built for everyday reliability."
    )

    defaults: dict = {
        # 1. Create App
        "app_name":             name[:30],
        "package_name":         application_id or "",
        "default_language":     "en-GB",
        "app_type":             "app",
        "is_free":              True,
        "policy_confirmed":     False,
        "signing_tos_accepted": False,
        "export_laws_accepted": False,
        # 2. App Content
        "app_access":           "all_available",
        "contains_ads":         False,
        # 3. Content Ratings
        "ratings_category":     "all_other",
        "ratings_email":        support_email,
        "q_violence":           False,
        "q_sexual":             False,
        "q_teen_rating":        False,
        "q_profanity":          False,
        "q_controlled_drugs":   False,
        "q_gambling":           False,
        "q_user_generated":     False,
        "q_account_sharing":    False,
        "q_location_sharing":   False,
        "ratings_iarc_agreed":  False,
        # 4. Target Audience
        "target_age":           "18_and_over",
        "legal_compliance":     False,
        # 5. Data Safety
        "data_collection":      False,
        "families_policy":      False,
        # 6. Policy Declarations
        "is_government":        False,
        "financial_features":   "none",
        "uses_advertising_id":  False,
        "health_features":      "none",
        # 7. Store Listing
        "app_category":         "BUSINESS",
        "contact_email":        support_email,
        "contact_website":      f"https://{domain_val}" if domain_val else "",
        "short_description":    short_desc,
        "full_description":     full_desc[:4000],
        # 8. Release
        "release_name":         "",
        "release_notes":        (
            f"<en-US>\n"
            f"Initial release of {display_name}.\n\n"
            f"• {role_noun.capitalize()} shift {role_verb_start} and {role_verb_end} time logging\n"
            f"• Weekly schedule overview\n"
            f"• Export {export_title} reports\n"
            f"• Designed for UK Working Time Regulations compliance\n"
            f"</en-US>"
        ),
    }

    f = _pc_file(cn, name)
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            defaults.update(saved)
        except Exception:
            pass

    if application_id and not defaults.get("package_name"):
        defaults["package_name"] = application_id

    return defaults


def _write_pc(cn: str, name: str, data: dict) -> None:
    f = _pc_file(cn, name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _build_pc(cn: str, name: str, app_id: str, manifest: dict,
              assigned_email: str, domain: str) -> dict:
    """Assemble Play Console data, backfilling release_name from version.json."""
    pc = _read_pc(cn, name, app_id, manifest=manifest,
                  assigned_email=assigned_email, domain=domain)
    if not pc.get("release_name"):
        ver = _read_version(cn, name)
        pc["release_name"] = ver.get("version_name", "1.0")
    return pc


def _rmdir(path: Path) -> None:
    """Remove a directory and all its contents, silently."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
    except Exception as e:
        print(f"  [rmdir] {path}: {e}")


def _clean_stage_output(cn: str, name: str, stage: str) -> None:
    """Delete prior output files for a stage before re-running it."""
    cdir = _co_dir(cn, name)
    if stage == "certificate":
        _rmdir(cdir / "certificate")
    elif stage == "director_id":
        _rmdir(cdir / "director_id")
    elif stage == "app":
        arts = cdir / "app" / "artifacts"
        if arts.is_dir():
            for f in arts.iterdir():
                if f.suffix in {".apk", ".aab"}:
                    try: f.unlink()
                    except Exception: pass


def _tree_node(path: Path) -> dict | None:
    """Recursive tree builder (no caching — called by _tree which caches root)."""
    if not path.exists():
        return None
    if path.is_file():
        return {"name": path.name, "type": "file",
                "size": path.stat().st_size, "path": str(path)}
    children: list[dict] = []
    try:
        for child in sorted(path.iterdir(),
                            key=lambda p: (p.is_file(), p.name.lower())):
            node = _tree_node(child)
            if node:
                children.append(node)
    except PermissionError:
        pass
    return {"name": path.name, "type": "dir", "children": children}


_TREE_CACHE: dict[str, tuple[float, dict | None]] = {}  # path → (ts, result)
_TREE_TTL = 10.0  # seconds


def _tree(path: Path) -> dict | None:
    """Return a nested tree dict, cached for _TREE_TTL seconds per root path."""
    key = str(path)
    now = time.monotonic()
    entry = _TREE_CACHE.get(key)
    if entry and now - entry[0] < _TREE_TTL:
        return entry[1]
    result = _tree_node(path)
    _TREE_CACHE[key] = (now, result)
    return result


# ── Retry helper ───────────────────────────────────────────────────────────

def _with_retry(fn, retries: int = 3, delay: float = 2.0):
    """Call fn(); on exception retry up to retries times with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait = delay * (2 ** attempt)
                print(f"  [retry {attempt + 1}/{retries}] {e} — retrying in {wait:.0f}s")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ── Core stage executor ────────────────────────────────────────────────────

def _exec_stage(cn: str, stage: str, row: dict):
    """Execute one stage. Called from thread. stdout already redirected."""
    name = str(row.get("Company Name") or "")
    sys.path.insert(0, str(ROOT))

    if stage == "details":
        from run_pipeline import get_company_details
        d = _with_retry(lambda: get_company_details(cn))
        _update_excel_row(cn, {
            "Company Name":           d.get("company_name", ""),
            "Status":                 d.get("company_status", ""),
            "Type":                   d.get("type", ""),
            "Date of Creation":       d.get("date_of_incorporation", ""),
            "SIC Codes":              d.get("sic_codes", ""),
            "Address":                d.get("address", ""),
            "Directors":              d.get("director_names", ""),
            "Director Nationalities": d.get("director_nationalities", ""),
            # Months and years, from the officers resource. This was written
            # from a key get_company_details never returned, so every run
            # replaced whatever was there with an empty string.
            "Director DOB":           d.get("director_dobs", ""),
            # Director Gender is deliberately absent: Companies House does
            # not publish gender, so this stage has nothing to say about it
            # and must not overwrite what a person entered by hand.
        })
        print(f"  {len(d.get('directors') or [])} serving director(s) "
              f"from {d.get('officers_seen', 0)} officer record(s)")
        return d

    elif stage == "duns":
        from run_pipeline import get_duns_number
        r = get_duns_number(cn, headless=True)
        if r.get("duns_number"):
            _update_excel_row(cn, {"DUNS Number": r["duns_number"], "DUNS Status": "found"})
        elif r.get("status") == "submitted":
            _update_excel_row(cn, {"DUNS Status": "submitted",
                                   "DUNS Email Used": r.get("temp_email", "")})
        return r

    elif stage == "certificate":
        _clean_stage_output(cn, name, "certificate")
        from run_pipeline import download_certificate
        out = str(_co_dir(cn, name) / "certificate")
        path, err = _with_retry(lambda: download_certificate(cn, out))
        # download_certificate reports failure by returning a reason, not by
        # raising, so _with_retry never saw it and the job was recorded as
        # done with the stage still showing Pending. Raise so the job says
        # what happened and run-all stops instead of carrying on.
        if not path:
            raise RuntimeError(err or "Certificate download failed")
        _update_excel_row(cn, {"Certificate Downloaded": True, "Certificate Path": path})
        return {"path": path}

    elif stage == "domain":
        from run_pipeline import register_company_domain
        details = {
            "company_name": name, "company_number": cn,
            "sic_codes": str(row.get("SIC Codes") or ""),
            "address": str(row.get("Address") or ""),
        }
        # Pass what the row already holds: registering is a purchase, and the
        # stage offers a re-run button once it is done.
        r = register_company_domain(name, details,
                                    current_domain=str(row.get("Domain") or ""))
        if r.get("status") == "exists":
            print(f"  {r.get('message')}")
            return r
        if r.get("domain"):
            _update_excel_row(cn, {"Domain": r["domain"],
                                   "Domain Status": r.get("status", ""),
                                   "Domain Cost": r.get("charged", "")})
        return r

    elif stage == "email":
        from email_pool import EmailPool
        pool = EmailPool()
        ex_email, ex_first, ex_last = pool.get_assigned_email(cn)
        if ex_email:
            # Already assigned in pool — sync to Excel if missing
            if not str(row.get("Assigned Email") or ""):
                _update_excel_row(cn, {"Assigned Email": ex_email,
                                       "Account Name": f"{ex_first} {ex_last}"})
            return {"email": ex_email, "status": "already_assigned"}
        email, first, last = pool.assign_next(company_number=cn, company_name=name)
        _update_excel_row(cn, {"Assigned Email": email,
                                "Account Name": f"{first} {last}"})
        return {"email": email, "status": "assigned"}

    elif stage == "director_id":
        _clean_stage_output(cn, name, "director_id")
        import pipeline as _pl
        _pl.stage_director_id(company_numbers=[cn])
        return {"status": "done"}

    elif stage == "app":
        _clean_stage_output(cn, name, "app")
        import pipeline as _pl
        _pl.stage_build(company_numbers=[cn])
        return {"status": "done"}

    elif stage == "publish":
        # Dry run from the dashboard, always. The edit is validated by Play
        # and discarded, which surfaces a bad listing, AAB or Data Safety
        # answer without spending a version code. Releasing is deliberate:
        #   python pipeline.py --stage publish --company <cn> --commit
        import play_publisher
        rep = play_publisher.publish(cn, track="internal", dry_run=True)
        for w in rep.warnings:
            print(f"  warn   {w}")
        if not rep.ok:
            raise RuntimeError("; ".join(rep.errors) or "publish preflight failed")
        print(f"  validated {rep.package_name} v{rep.version_code} on Play — "
              f"the bundle and listing were uploaded to a draft edit and the edit "
              f"was discarded, so nothing was released.")
        print(f"  To release:  python pipeline.py --stage publish "
              f"--company {cn} --commit")
        return rep.as_dict()

    raise ValueError(f"Unknown stage: {stage}")


# ── Routes ─────────────────────────────────────────────────────────────────

@api_v2.route("/app")
def app_page():
    return send_file(ROOT / "static" / "app.html")


@api_v2.route("/portal")
def portal_page():
    """
    The client's page.

    Separate from /app rather than /app with the controls hidden: a hidden
    button is still a reachable endpoint, and the operator console calls
    endpoints a client is refused. auth.CLIENT_ENDPOINTS lists what this page
    is allowed to call.
    """
    return send_file(ROOT / "static" / "portal.html")


@api_v2.route("/api/v2/companies")
def v2_companies():
    rows = _all_rows()
    archived_param = request.args.get("archived", "false").lower()
    show_archived = archived_param == "true"

    # Every active job for this tenant in one query. The dashboard polls this
    # route every 2.5s; asking per company per stage was 7xN round trips a tick.
    active = db.active_jobs(_tenant())

    out = []
    for row in rows:
        cn = _cn8(row.get("Company Number"))
        if not cn or cn == "00000000": continue
        
        # Filter by archive status
        archived_val = str(row.get("Archived") or "").strip().lower()
        is_archived = archived_val in ("yes", "true", "1")
        if is_archived != show_archived:
            continue
        
        stages = _compute_stages(cn, row)
        running = active.get(cn, [])
        company_status = str(row.get("Company Status") or "").strip() or "Active"
        
        out.append({
            "cn":              cn,
            "name":            str(row.get("Company Name") or ""),
            "domain":          str(row.get("Domain") or ""),
            "stages":          stages,
            "n_done":          sum(stages.values()),
            "running":         running,
            "company_status":  company_status,
            "notes":           str(row.get("Company Notes") or "").strip(),
            "archived":        is_archived,
        })
    return jsonify(out)


@api_v2.route("/api/v2/company/<cn>")
def v2_company(cn):
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Not found"}), 404

    name     = str(row.get("Company Name") or "")
    cdir     = _co_dir(cn, name)
    stages   = _compute_stages(cn, row)
    manifest    = _read_manifest(cn, name)
    app_id      = str(manifest.get("application_id") or "")
    privacy_url = str(manifest.get("privacy_policy_url") or "")
    a_email     = str(row.get("Assigned Email") or "")
    domain_val  = str(row.get("Domain") or "")

    # One query for every stage. Polled every 2.5s, so a query per stage was
    # seven round trips per company per tick.
    jobs = {
        stage: {k: v for k, v in job.items() if k != "log"}
        for stage, job in db.jobs_for_company(_tenant(), cn).items()
    }

    return jsonify({
        "cn":               cn,
        "name":             name,
        "publish":          _publish_summary(cn),
        "status":           str(row.get("Status") or ""),
        "type":             str(row.get("Type") or ""),
        "sic":              str(row.get("SIC Codes") or ""),
        "address":          str(row.get("Address") or ""),
        "directors":        str(row.get("Directors") or ""),
        "nationalities":    str(row.get("Director Nationalities") or ""),
        "dobs":             str(row.get("Director DOB") or ""),
        "genders":          str(row.get("Director Gender") or ""),
        "buvei_first":      str(row.get("Buvei First Name") or ""),
        "buvei_last":       str(row.get("Buvei Last Name") or ""),
        "duns":             str(row.get("DUNS Number") or ""),
        "duns_status":      str(row.get("DUNS Status") or ""),
        "duns_email":       str(row.get("DUNS Email Used") or ""),
        "dnb_name":         str(row.get("D&B Legal Name") or ""),
        "dnb_address":      str(row.get("D&B Address") or ""),
        "date_of_creation": str(row.get("Date of Creation") or ""),
        "domain":           str(row.get("Domain") or ""),
        "domain_status":    str(row.get("Domain Status") or ""),
        "domain_cost":      str(row.get("Domain Cost") or ""),
        "email":            str(row.get("Assigned Email") or ""),
        "account_name":     str(row.get("Account Name") or ""),
        "developer_email":  str(row.get("Developer Email") or ""),
        "dev_email_forwarding": str(row.get("Dev Email Forwarding") or ""),
        "google_txt_status": str(row.get("Google TXT Status") or ""),
        "organization_phone": str(row.get("Organization Phone") or ""),
        "certificate_path": str(row.get("Certificate Path") or ""),
        "certificate_downloaded": str(row.get("Certificate Downloaded") or ""),
        "application_id":   app_id,
        "privacy_policy_url": privacy_url,
        "company_status":   str(row.get("Company Status") or "").strip() or "Active",
        "notes":            str(row.get("Company Notes") or "").strip(),
        "archived":         str(row.get("Archived") or "").strip().lower() in ("yes", "true", "1"),
        "stages":           stages,
        "files": {
            "certificate":   _ls(cdir / "certificate", exts=[".pdf"]),
            "director_id":   _ls(cdir / "director_id",
                                 names=["ID Front.jpeg", "ID Back.jpeg", "ID_Combined.jpeg"]),
            "app":           _ls(cdir / "app" / "artifacts", exts=[".apk", ".aab"]),
            "payment_profile": [_pp(cdir)] if _pp(cdir) else [],
        },
        "tree":             _tree(cdir),
        "version":          _read_version(cn, name),
        "play_console":     _build_pc(cn, name, app_id, manifest, a_email, domain_val),
        "jobs":             jobs,
    })


@api_v2.route("/api/v2/company/<cn>/fields", methods=["POST"])
@operator_required
def v2_update_fields(cn):
    """Manually override one or more company fields. Values are written to the
    company row; stage status and all info panels are recomputed on the next
    fetch, so a manual edit (e.g. setting Domain) propagates everywhere."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    fields = body.get("fields", body)
    if not isinstance(fields, dict):
        return jsonify({"error": "fields must be an object"}), 400

    updates = {}
    for key, val in fields.items():
        header = EDITABLE_FIELDS.get(key)
        if header is None:
            continue
        updates[header] = "" if val is None else str(val).strip()

    if not updates:
        return jsonify({"error": "no editable fields provided"}), 400

    _update_excel_row(cn, updates)
    fresh  = _find_row(cn) or {}
    stages = _compute_stages(cn, fresh)
    return jsonify({"status": "ok", "cn": cn,
                    "updated": list(updates.keys()), "stages": stages}), 200


@api_v2.route("/api/v2/company/<cn>/stage/<stage>", methods=["POST"])
@operator_required
def v2_run_stage(cn, stage):
    if stage not in STAGE_ORDER:
        return jsonify({"error": f"Unknown stage: {stage}"}), 400

    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404

    # The claim inside _spawn is the guard: one statement, so two clicks
    # landing together cannot both start the stage.
    if not _spawn(cn, stage, lambda: _exec_stage(cn, stage, _find_row(cn) or row)):
        return jsonify({"error": "Already running"}), 409
    return jsonify({"status": "started", "cn": cn, "stage": stage})


@api_v2.route("/api/v2/company/<cn>/run-all", methods=["POST"])
@operator_required
def v2_run_all(cn):
    """Run all pending stages in order for one company."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404

    running = db.active_jobs(_tenant()).get(cn, [])
    if running:
        return jsonify({"error": f"'{running[0]}' is already running"}), 409

    tenant_id = _tenant()   # read here: the worker thread will not inherit it

    def _seq():
        with _co_lock(cn):
            for stage in STAGE_ORDER:
                with db.tenant_scope(tenant_id):
                    fresh = _find_row(cn) or {}
                    already_done = _compute_stages(cn, fresh).get(stage)
                if already_done:
                    continue
                initial = "queued" if stage in _HEAVY_STAGES else "running"
                if not db.job_claim(tenant_id, cn, stage, WORKER_ID, status=initial):
                    break  # someone else is running this stage
                try:
                    _run_tracked(
                        tenant_id, cn, stage,
                        lambda s=stage, f=fresh: _exec_stage(cn, s, _find_row(cn) or f),
                    )
                except Exception:
                    break  # stop on first failure; it is recorded on the job

    threading.Thread(target=_seq, daemon=True).start()
    return jsonify({"status": "started"})


@api_v2.route("/api/v2/company/<cn>/job/<stage>")
def v2_job(cn, stage):
    # Ownership check: the job carries the stage's captured stdout, which
    # includes directors, assigned emails, domains and DUNS numbers. Without
    # this, any signed-in user could read any company's log by guessing a
    # number — every other route on this blueprint resolves the row first.
    if not _find_row(cn):
        return jsonify({"error": "Company not found"}), 404
    j = _job_get(cn, stage)
    return jsonify(j if j else {"status": "idle"})


@api_v2.route("/api/v2/company/search")
def v2_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    ql = q.lower()

    # Optional year-of-registration filter: ?years=2020,2021,2022
    years_param = request.args.get("years", "").strip()
    year_filter = {y.strip() for y in years_param.split(",") if y.strip()}

    rows = _all_rows()
    results = []

    for row in rows:
        cn    = _cn8(row.get("Company Number"))
        name  = str(row.get("Company Name") or "")
        dirs  = str(row.get("Directors") or "")
        if ql in cn or ql in name.lower() or ql in dirs.lower():
            stages = _compute_stages(cn, row)
            doc    = str(row.get("Date of Creation") or "")
            results.append({
                "cn":        cn,
                "name":      name,
                "directors": dirs,
                "n_done":    sum(stages.values()),
                "stages":    stages,
                "source":    "local",
                "date_of_creation": doc,
                "year":      doc[:4] if doc else "",
            })

    # Apply year filter to local matches (so CH fallback still runs if none match)
    if year_filter:
        results = [r for r in results if (r.get("year") or "") in year_filter]

    # Fall back to CH API if nothing found locally
    ch_error = None
    if not results:
        try:
            sys.path.insert(0, str(ROOT))
            from run_pipeline import ch_get
            # Params, not an f-string. Interpolating the query meant a
            # company name containing "&" — "H & M", "Marks & Spencer" —
            # terminated the q parameter and the rest became stray
            # parameters, so the search silently looked for something else.
            data = ch_get("/search/companies", {"q": q, "items_per_page": 20})
            for item in (data.get("items") or []):
                if item.get("company_status", "").lower() != "active": continue
                item_cn = _cn8(item.get("company_number", ""))
                if any(c["cn"] == item_cn for c in results): continue
                inc = item.get("date_of_creation", "") or ""
                yr  = inc[:4] if inc else ""
                if year_filter and yr not in year_filter:
                    continue
                results.append({
                    "cn":        item_cn,
                    "name":      item.get("title", ""),
                    "directors": "",
                    "n_done":    0,
                    "stages":    {s: False for s in STAGE_ORDER},
                    "source":    "ch_api",
                    "date_of_creation": inc,
                    "year":      yr,
                })
        except Exception as e:
            ch_error = str(e)
            print(f"  CH search error: {e}")

    resp: dict = {"results": results[:10]}
    if ch_error:
        resp["ch_error"] = ch_error
    return jsonify(resp)


@api_v2.route("/api/v2/company/add", methods=["POST"])
@operator_required
def v2_add_company():
    data = request.get_json(force=True) or {}
    cn   = _cn8(data.get("cn") or data.get("company_number") or "")
    name = str(data.get("name") or data.get("company_name") or "").strip()
    # Year of registration (accept full date, or bare year from the dropdown)
    doc  = str(data.get("date_of_creation") or data.get("year") or "").strip()
    if not cn or cn == "00000000":
        return jsonify({"error": "company_number required"}), 400

    existing = _find_row(cn)
    if existing:
        return jsonify({"status": "exists", "cn": cn, "name": str(existing.get("Company Name") or "")}), 200

    _add_company_row(cn, name, doc)
    return jsonify({"status": "added", "cn": cn, "name": name, "date_of_creation": doc}), 201


@api_v2.route("/api/v2/company/<cn>/version", methods=["POST"])
@operator_required
def v2_set_version(cn):
    """Save version_code + version_name for a company (does NOT rebuild — caller triggers that)."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    vc = body.get("version_code")
    vn = str(body.get("version_name", "")).strip()
    if not isinstance(vc, int) or vc < 1:
        return jsonify({"error": "version_code must be a positive integer"}), 400
    if not vn:
        return jsonify({"error": "version_name is required"}), 400
    name = str(row.get("Company Name") or "")
    _write_version(cn, name, vc, vn)
    return jsonify({"status": "saved", "version_code": vc, "version_name": vn})


@api_v2.route("/api/v2/company/<cn>/play-console", methods=["POST"])
@operator_required
def v2_save_play_console(cn):
    """Save all Play Console form sections for a company."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    name = str(row.get("Company Name") or "")

    app_name = str(body.get("app_name", "")).strip()
    if not app_name:
        return jsonify({"error": "app_name is required"}), 400
    if len(app_name) > 30:
        return jsonify({"error": "app_name must be ≤ 30 characters"}), 400
    pkg = str(body.get("package_name", "")).strip()
    if len(pkg) > 150:
        return jsonify({"error": "package_name must be ≤ 150 characters"}), 400
    short_desc = str(body.get("short_description", ""))
    if len(short_desc) > 80:
        return jsonify({"error": "short_description must be ≤ 80 characters"}), 400
    full_desc = str(body.get("full_description", ""))
    if len(full_desc) > 4000:
        return jsonify({"error": "full_description must be ≤ 4000 characters"}), 400

    _STR = [
        "app_name", "package_name", "default_language", "app_type", "app_access",
        "ratings_category", "ratings_email", "target_age", "financial_features",
        "health_features", "app_category", "contact_email", "contact_website",
        "short_description", "full_description",
        "release_name", "release_notes",
    ]
    _BOOL = [
        "is_free", "policy_confirmed", "signing_tos_accepted", "export_laws_accepted",
        "contains_ads", "q_violence", "q_sexual", "q_teen_rating", "q_profanity",
        "q_controlled_drugs", "q_gambling", "q_user_generated", "q_account_sharing",
        "q_location_sharing", "ratings_iarc_agreed", "legal_compliance",
        "data_collection", "families_policy", "is_government", "uses_advertising_id",
    ]
    data: dict = {}
    for f in _STR:
        data[f] = str(body.get(f, "")).strip()
    for f in _BOOL:
        data[f] = bool(body.get(f, False))

    _write_pc(cn, name, data)
    return jsonify({"status": "saved", **data})


@api_v2.route("/api/v2/company/<cn>/rerun-id", methods=["POST"])
@operator_required
def v2_rerun_id(cn):
    """Delete existing director ID files and regenerate a new one via Xbinder."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    if _job_get(cn, "director_id").get("status") in db.ACTIVE_JOB_STATUSES:  # noqa: SIM102
        return jsonify({"error": "Already running"}), 409
    _spawn(cn, "director_id", lambda: _exec_stage(cn, "director_id", _find_row(cn) or row))
    return jsonify({"status": "started", "cn": cn, "stage": "director_id"})


@api_v2.route("/api/v2/dl")
@api_v2.route("/api/v2/dl/<label>")
def v2_download(label=None):
    """Serve one of this tenant's company files.

    auth.tenant_company_file enforces both that the path stays inside
    pipeline_output/companies and that the signed-in tenant owns the company.

    ?inline=1  — serve inline (for in-browser preview); default is attachment download.

    The optional /<label> segment is cosmetic and never used to find the
    file: Chrome's embedded PDF viewer titles itself from the last path
    segment, not from Content-Disposition, so without it the certificate
    preview is headed "dl". The file always comes from ?path, which is what
    tenant_company_file checks.
    """
    from auth import tenant_company_file

    path = request.args.get("path", "").strip()
    if not path:
        return abort(400)
    resolved = tenant_company_file(path)
    if resolved is None:
        return abort(404)
    inline = request.args.get("inline", "0") == "1"
    # download_name even when inline: without a filename in the
    # Content-Disposition, the browser's PDF viewer titles the tab from the
    # URL and shows "dl" above the certificate.
    return send_file(resolved, as_attachment=not inline, download_name=resolved.name)


@api_v2.route("/api/v2/company/<cn>/archive", methods=["POST"])
@operator_required
def v2_archive_company(cn):
    """Archive a company (hide from main view, show in archive tab)."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    _update_excel_row(cn, {"Archived": "Yes"})
    return jsonify({"status": "archived", "cn": cn, "archived": True})


@api_v2.route("/api/v2/company/<cn>/unarchive", methods=["POST"])
@operator_required
def v2_unarchive_company(cn):
    """Unarchive a company (show in main view)."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    _update_excel_row(cn, {"Archived": "No"})
    return jsonify({"status": "unarchived", "cn": cn, "archived": False})


@api_v2.route("/api/v2/company/<cn>/status", methods=["POST"])
@operator_required
def v2_update_status(cn):
    """Update company status (Active, Declined, Pending, On Hold)."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    
    body = request.json or {}
    status = str(body.get("status", "")).strip()
    valid_statuses = ["Active", "Declined", "Pending", "On Hold"]
    
    if status and status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"}), 400
    
    _update_excel_row(cn, {"Company Status": status})
    return jsonify({"status": "updated", "cn": cn, "company_status": status})


@api_v2.route("/api/v2/company/<cn>/notes", methods=["POST"])
@operator_required
def v2_update_notes(cn):
    """Update company notes (max 1000 characters)."""
    row = _find_row(cn)
    if not row:
        return jsonify({"error": "Company not found"}), 404
    
    body = request.json or {}
    notes = str(body.get("notes", "")).strip()
    
    if len(notes) > 1000:
        return jsonify({"error": "Notes must be 1000 characters or less"}), 400
    
    _update_excel_row(cn, {"Company Notes": notes})
    return jsonify({"status": "updated", "cn": cn, "notes": notes})


@api_v2.route("/api/v2/me")
def v2_me():
    """
    Who is signed in, for the page that is rendering.

    Every surface needs this and none had it: the portal rendered an empty
    name field, and the operator console could not show an admin the link to
    their own console because it had no idea what role it was serving.

    It reports the signed-in user and takes no argument — there is no way to
    ask about somebody else. The role is already decided server-side by
    auth's gate; sending it here only lets a page stop offering what it
    would be refused.
    """
    from flask import g
    user = g.user or {}
    tenant = {}
    try:
        tenant = db.get_tenant(int(user.get("tenant_id"))) or {}
    except (TypeError, ValueError):
        pass
    return jsonify({
        "email": user.get("email", ""),
        "role": user.get("role", "client"),
        "tenant": tenant.get("slug", ""),
        "tenant_name": tenant.get("name", ""),
    })


@api_v2.route("/health")
def v2_health():
    return jsonify({"status": "ok", "ts": datetime.now().isoformat()})


# Runs once per process, after WORKER_ID exists. Any job still marked running
# or queued belongs to a process that is no longer here.
reclaim_interrupted_jobs()
