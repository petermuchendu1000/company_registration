"""Operator-facing Excel import for the admin dashboard.

Kept in its own module deliberately. This is the only path by which
operator-supplied data enters the companies table, and the only path that
can overwrite a live domain purchase claim, so its blast radius is worth
keeping legible rather than folding into api_v2's 1000 lines.

The flow is two-step on purpose:

    GET  /api/import/template   a workbook with the exact headers expected
    POST /api/import/preview    upload -> db.diff_from_excel -> report
    POST /api/import/apply      token  -> db.import_from_excel

A one-shot upload-and-import would throw away the reason diff_from_excel
exists. Blank cells are already skipped by db._excel_records, so an import
cannot erase a column. But a POPULATED yet STALE cell still replaces a
populated column, because that is what an import is for, and
db.DOMAIN_CLAIM sitting in domain_status is what stops run_pipeline buying
a second domain for a company that has already been charged for one. Only
a person can tell stale from authoritative, so the diff is shown and the
write waits for a decision.

apply re-runs the diff rather than trusting the token's earlier verdict.
Pipeline stages run in background threads (api_v2.py:173), so a purchase
claim can appear in the window between the operator reading a preview and
pressing the button. Checking at preview time only would be a
time-of-check/time-of-use gap on exactly the value that prevents a double
charge.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import time
import uuid

from flask import Blueprint, jsonify, request, send_file

import db

log = logging.getLogger(__name__)

# Dedicated audit channel, same one run_pipeline writes a domain purchase to.
# An import that overwrites money columns belongs in that trail, not in the
# general application log.
_money_log = logging.getLogger("money")

imports = Blueprint("imports", __name__)


class _TooLarge(Exception):
    """Raised internally to abort a streamed upload past the size cap."""


MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # a 35-column sheet is ~10 KB/1000 rows
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")
PENDING_TTL = 30 * 60                    # an unapplied upload expires

# token -> {path, ts, filename}. In-memory like the DUNS job registry, which
# is why gunicorn.conf.py pins workers = 1; a second worker would not see
# these tokens and every apply would 404.
_PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()

_UPLOAD_DIR: str | None = None
_UPLOAD_DIR_LOCK = threading.Lock()


def _upload_dir() -> str:
    """A private directory for uploads, created once per process.

    mkdtemp, not a fixed path under /tmp: it returns a uniquely named
    directory created with mode 0700 in one atomic step. A predictable path
    in a world-writable sticky directory can be pre-created or symlinked by
    another local user before we get there.
    """
    global _UPLOAD_DIR
    if _UPLOAD_DIR is None:
        with _UPLOAD_DIR_LOCK:
            if _UPLOAD_DIR is None:
                _UPLOAD_DIR = tempfile.mkdtemp(prefix="cr_import_")
    return _UPLOAD_DIR


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _reap_expired() -> None:
    now = time.time()
    with _PENDING_LOCK:
        stale = [t for t, e in _PENDING.items() if now - e["ts"] > PENDING_TTL]
        entries = [_PENDING.pop(t) for t in stale]
    for e in entries:
        _unlink(e["path"])


def _discard(token: str) -> None:
    with _PENDING_LOCK:
        entry = _PENDING.pop(token, None)
    if entry:
        _unlink(entry["path"])


# ── Template ────────────────────────────────────────────────────────────────
# The example row carries company number 00000000, which db._excel_records
# skips explicitly (db.py:581). Leaving it in the sheet therefore cannot
# create a company. That is why the example is safe to ship filled in rather
# than as a comment the operator has to delete.
EXAMPLE = {
    "No.": 1,
    "Company Number": "00000000",
    "Company Name": "EXAMPLE ROW - DELETE OR LEAVE, IT IS IGNORED",
    "Short Name": "Example",
    "Status": "Active",
    "Type": "ltd",
    "Date of Creation": "2024-01-31",
    "SIC Codes": "62012",
    "Address": "1 Example Street, London, EC1A 1AA",
    "Directors": "Jane Doe",
    "Director Nationalities": "British",
    "Director DOB": "1985-04",
    "Director Gender": "F",
}

NOTES = [
    ("How this sheet is read", ""),
    ("", ""),
    ("Header row", "Row 1 must keep these exact header labels. They are matched by "
                   "name, not by position, so you may reorder or delete columns."),
    ("Company Number", "Required. This is the key. Fewer than 8 digits is "
                       "zero-padded automatically, so 12345 becomes 00012345."),
    ("Blank cells", "Skipped, never written. A blank cell means 'this sheet does "
                    "not know', not 'clear the stored value'. You cannot erase "
                    "data by clearing a cell."),
    ("Populated cells", "Overwrite whatever is stored. This is what an import is "
                        "for, and it is why the dashboard shows you a field-level "
                        "diff before anything is written."),
    ("Domain Status", "Leave alone unless you know why you are changing it. The "
                      "value 'purchasing' is a live claim meaning a domain may "
                      "already have been bought and PAID FOR. Overwriting it lets "
                      "the pipeline buy and pay a second time. The import refuses "
                      "to clear one unless you explicitly override."),
    ("Example row", "Company number 00000000 is ignored by the importer, so the "
                    "example row below the header cannot create a company."),
    ("Extra columns", "Ignored. Add your own working columns if you like."),
    ("Extra rows", "A row with no Company Number is skipped."),
]


def _build_template() -> io.BytesIO:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Companies"
    headers = [h for h, _ in db.COLUMNS]
    ws.append(headers)

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="2F5597")
    money_fill = PatternFill("solid", fgColor="A6341F")
    money_headers = {db.COL_TO_HEADER[c] for c in db.MONEY_COLUMNS}

    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = head_font
        # Money columns are tinted so the person filling the sheet can see,
        # without reading the notes, which four columns can cost real money.
        cell.fill = money_fill if h in money_headers else head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(len(h) + 6, 34))

    ws.append([EXAMPLE.get(h, "") for h in headers])
    ws.freeze_panes = "C2"          # keep No. + Company Number visible
    ws.row_dimensions[1].height = 32

    notes = wb.create_sheet("How to fill")
    notes.column_dimensions["A"].width = 22
    notes.column_dimensions["B"].width = 100
    for a, b in NOTES:
        notes.append([a, b])
    for row in notes.iter_rows(min_col=1, max_col=1):
        for c in row:
            c.font = Font(bold=True)
    for row in notes.iter_rows(min_col=2, max_col=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    buf.seek(0)
    return buf


@imports.route("/api/import/template", methods=["GET"])
def import_template():
    """Download a workbook with the exact headers the importer expects."""
    try:
        buf = _build_template()
    except Exception as e:                      # pragma: no cover - defensive
        log.exception("template build failed")
        return jsonify({"error": f"could not build template: {e}"}), 500
    return send_file(
        buf,
        as_attachment=True,
        download_name="company_import_template.xlsx",
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
    )


# ── Preview ─────────────────────────────────────────────────────────────────
@imports.route("/api/import/preview", methods=["POST"])
def import_preview():
    """Store an uploaded workbook and report exactly what importing it does.

    Writes nothing to the companies table.
    """
    _reap_expired()

    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "no file uploaded (the form field must be "
                                 "named 'file')"}), 400

    suffix = os.path.splitext(f.filename or "")[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify({"error": f"expected {' or '.join(ALLOWED_SUFFIXES)}, "
                                 f"got '{suffix or 'no extension'}'"}), 400

    # The client-supplied filename never reaches the filesystem. The path is
    # built from a server-generated token plus a suffix we just allow-listed,
    # so traversal, absolute paths and NUL bytes in the name are all moot.
    token = uuid.uuid4().hex
    path = os.path.join(_upload_dir(), token + suffix)

    written = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = f.stream.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise _TooLarge()
                out.write(chunk)
    except _TooLarge:
        _unlink(path)
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return jsonify({"error": f"file exceeds the {mb} MB limit"}), 413
    except Exception as e:
        _unlink(path)
        return jsonify({"error": f"could not store the upload: {e}"}), 400

    # Streamed in bounded chunks and capped BEFORE openpyxl opens it: a zip
    # bomb has to get past the size limit first.
    try:
        diff = db.diff_from_excel(path)
    except Exception as e:
        _unlink(path)
        return jsonify({"error": f"could not read the workbook: {e}"}), 400

    with _PENDING_LOCK:
        _PENDING[token] = {"path": path, "ts": time.time(),
                           "filename": f.filename or "upload.xlsx"}

    log.info("import preview token=%s file=%r bytes=%d new=%d additions=%d "
             "overwrites=%d money=%d claims_at_risk=%d",
             token, f.filename, written, len(diff["new_companies"]),
             diff["additions"], diff["overwrites"],
             len(diff["money_overwrites"]), len(diff["claims_at_risk"]))

    return jsonify({
        "token": token,
        "filename": f.filename,
        "bytes": written,
        "expires_in": PENDING_TTL,
        "diff": diff,
    })


# ── Apply ───────────────────────────────────────────────────────────────────
@imports.route("/api/import/apply", methods=["POST"])
def import_apply():
    """Write a previously previewed workbook into the companies table."""
    _reap_expired()

    body = request.get_json(silent=True) or {}
    token = str(body.get("token") or "")
    force = bool(body.get("force"))

    with _PENDING_LOCK:
        entry = _PENDING.get(token)
    if entry is None:
        return jsonify({"error": "unknown or expired upload - please upload "
                                 "the file again"}), 404
    path = entry["path"]
    if not os.path.exists(path):
        _discard(token)
        return jsonify({"error": "the uploaded file is no longer on disk - "
                                 "please upload it again"}), 410

    # Re-read NOW. The preview the operator looked at may be minutes old and
    # pipeline stages write from background threads, so a purchase claim can
    # have appeared since. Deciding on the stale preview would be a
    # time-of-check/time-of-use gap on the one value that prevents paying
    # twice for the same domain.
    try:
        diff = db.diff_from_excel(path)
    except Exception as e:
        return jsonify({"error": f"could not re-read the workbook: {e}"}), 400

    if diff["claims_at_risk"] and not force:
        return jsonify({
            "error": "refused: this sheet would overwrite an outstanding "
                     "domain purchase claim",
            "claims_at_risk": diff["claims_at_risk"],
            "diff": diff,
            "hint": "a claim of 'purchasing' means a domain may already have "
                    "been bought and charged. Clearing it lets the pipeline "
                    "buy and pay a second time. Re-send with force=true only "
                    "if you have checked the registrar.",
        }), 409

    if diff["money_overwrites"] or diff["claims_at_risk"]:
        _money_log.warning(
            "excel import WRITING money columns: file=%r token=%s "
            "money_overwrites=%d claims_at_risk=%s force=%s detail=%s",
            entry["filename"], token, len(diff["money_overwrites"]),
            diff["claims_at_risk"], force, diff["money_overwrites"],
        )

    try:
        imported = db.import_from_excel(path)
    except Exception as e:
        log.exception("import apply failed token=%s", token)
        return jsonify({"error": f"import failed, nothing was committed: {e}"}), 500
    finally:
        # The workbook has served its purpose either way; a retry re-uploads.
        _discard(token)

    log.info("import applied token=%s file=%r rows=%d", token,
             entry["filename"], imported)

    return jsonify({
        "imported": imported,
        "new_companies": diff["new_companies"],
        "additions": diff["additions"],
        "overwrites": diff["overwrites"],
        "money_overwrites": diff["money_overwrites"],
        "forced": force,
    })


@imports.route("/api/import/discard", methods=["POST"])
def import_discard():
    """Drop an uploaded workbook without importing it."""
    body = request.get_json(silent=True) or {}
    _discard(str(body.get("token") or ""))
    return jsonify({"discarded": True})
