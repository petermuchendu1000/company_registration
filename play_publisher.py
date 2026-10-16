"""
Google Play Android Publisher API client — automates everything that can be
automated *after* a developer account exists and the app has been created.

What this module does (all via the official androidpublisher v3 API, no browser
automation):

    - uploads the signed AAB produced by apps/generator
    - writes the localized store listing (title, short/full description)
    - uploads icon, feature graphic and phone/tablet screenshots
    - assigns the new version code to a release track with release notes
    - validates the whole edit, and only then commits it

What this module deliberately does NOT do:

    - create or verify a developer account (no API exists; identity, OTP,
      payment and device verification are human steps by design)
    - complete the IARC content-rating questionnaire (no API)
    - submit identity documents anywhere

Credentials
-----------
Each client's Play Console issues its own service account. Drop the JSON key at

    {PLAY_SERVICE_ACCOUNTS_DIR}/{company_number}.json

(default `secrets/play_service_accounts/`, git-ignored). In Play Console the
service account must be invited under Users & Permissions with release
permissions for the target app.

Note: Google requires the *first* release of a new app to be made manually
through the Play Console UI; API releases work from the second onward. A first
upload therefore fails with a clear error from `publish()` rather than silently.

Public API:
    preflight(company_number)                 -> Report  (no network calls)
    publish(company_number, track=..., ...)   -> Report  (validates; commits
                                                 only when dry_run is False)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# Play's hard limits on listing text.
TITLE_MAX = 30
SHORT_DESC_MAX = 80
FULL_DESC_MAX = 4000

VALID_TRACKS = {"internal", "alpha", "beta", "production"}

# play_listing.json graphics key -> androidpublisher AppImageType enum value.
# Enum values per AppImageType: phoneScreenshots, sevenInchScreenshots,
# tenInchScreenshots, tvScreenshots, wearScreenshots, icon, featureGraphic,
# tvBanner.
IMAGE_TYPES: list[tuple[str, str, bool]] = [
    # (listing key,                  AppImageType,            is_list)
    ("app_icon",                     "icon",                  False),
    ("feature_graphic",              "featureGraphic",        False),
    ("phone_screenshots",            "phoneScreenshots",      True),
    ("tablet_7inch_screenshots",     "sevenInchScreenshots",  True),
    ("tablet_10inch_screenshots",    "tenInchScreenshots",    True),
]


def _accounts_dir() -> Path:
    return Path(
        os.getenv("PLAY_SERVICE_ACCOUNTS_DIR")
        or Path(__file__).parent / "secrets" / "play_service_accounts"
    )


def _companies_dir() -> Path:
    """This tenant's subtree of the companies root. See company_paths."""
    import company_paths
    return company_paths.tenant_root()


@dataclass
class Report:
    """Outcome of a preflight or publish run."""
    company_number: str
    ok: bool = True
    dry_run: bool = True
    package_name: str = ""
    version_code: int | None = None
    track: str = ""
    edit_id: str = ""
    committed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uploaded_images: dict[str, int] = field(default_factory=dict)

    def fail(self, msg: str) -> "Report":
        self.ok = False
        self.errors.append(msg)
        return self

    def warn(self, msg: str) -> "Report":
        self.warnings.append(msg)
        return self

    def as_dict(self) -> dict:
        return {
            "company_number": self.company_number,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "package_name": self.package_name,
            "version_code": self.version_code,
            "track": self.track,
            "edit_id": self.edit_id,
            "committed": self.committed,
            "errors": self.errors,
            "warnings": self.warnings,
            "uploaded_images": self.uploaded_images,
        }


# ── Locating this company's generated artefacts ─────────────────────────────
def _app_dir(company_number: str) -> Path | None:
    """
    This tenant's app directory for a company; the name varies, so the
    lookup globs on the number — but inside the tenant's own subtree, not
    across the whole companies root, which is what previously let one
    tenant's build answer for another's.
    """
    import company_paths
    found = company_paths.find_company_dir(company_number)
    return (found / "app") if found else None


def _listing_path(app_dir: Path) -> Path:
    """
    assets.generate_all() writes listing files into {app}/listing/. Older
    pipeline versions wrote them flat into {app}/, so fall back to that.
    """
    nested = app_dir / "listing" / "play_listing.json"
    return nested if nested.is_file() else app_dir / "play_listing.json"


def _expected_listing_path(app_dir: Path) -> Path:
    """
    Where the generator writes the listing — the path an error should name.

    _listing_path falls back to the legacy flat location when neither file
    exists, so using it in a "missing file" message pointed the reader at a
    directory nothing has written to since assets.generate_all() moved.
    """
    return app_dir / "listing" / "play_listing.json"


def _data_safety_path(app_dir: Path) -> Path:
    nested = app_dir / "listing" / "data_safety.json"
    return nested if nested.is_file() else app_dir / "data_safety.json"


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable %s: %s", path, exc)
        return None


def _load_listing(app_dir: Path) -> dict | None:
    return _load_json(_listing_path(app_dir))


def _find_aab(app_dir: Path) -> Path | None:
    hits = sorted((app_dir / "artifacts").glob("*.aab")) if (app_dir / "artifacts").is_dir() else []
    return hits[0] if hits else None


def _lang(listing: dict) -> str:
    """Map 'English (United Kingdom) - en-GB' to the BCP-47 tag the API wants."""
    raw = (listing.get("app_identity", {}) or {}).get("default_language", "")
    if "-" in raw:
        tag = raw.rsplit("-", 1)[-1].strip()
        if tag:
            return tag
    return "en-GB"


# ── Preflight: catch every problem before touching the network ──────────────
def preflight(company_number: str) -> Report:
    """Validate local artefacts and listing text. Makes no API calls."""
    cn = str(company_number).zfill(8)
    rep = Report(company_number=cn)

    app_dir = _app_dir(cn)
    if app_dir is None or not app_dir.is_dir():
        return rep.fail(f"No generated app directory for company {cn}")

    key = _accounts_dir() / f"{cn}.json"
    if not key.is_file():
        rep.fail(f"Missing Play service account key: {key}")

    listing = _load_listing(app_dir)
    if listing is None:
        return rep.fail(f"Missing or unreadable {_expected_listing_path(app_dir)} "
                        f"— run the app stage to generate it")

    identity = listing.get("app_identity", {}) or {}
    store = listing.get("store_listing", {}) or {}

    pkg = identity.get("package_name") or ""
    rep.package_name = pkg
    if not pkg:
        rep.fail("play_listing.json has no app_identity.package_name")

    title = identity.get("app_name") or ""
    short = store.get("short_description") or ""
    full = store.get("full_description") or ""

    if not title:
        rep.fail("Store listing title is empty")
    elif len(title) > TITLE_MAX:
        rep.fail(f"Title is {len(title)} chars, max {TITLE_MAX}")

    if not short:
        rep.fail("Short description is empty")
    elif len(short) > SHORT_DESC_MAX:
        rep.fail(f"Short description is {len(short)} chars, max {SHORT_DESC_MAX}")

    if not full:
        rep.fail("Full description is empty")
    elif len(full) > FULL_DESC_MAX:
        rep.fail(f"Full description is {len(full)} chars, max {FULL_DESC_MAX}")

    # Placeholders that assets.py emits when upstream data is still missing.
    for label, value in (
        ("privacy policy URL", store.get("privacy_policy_url")),
        ("developer website", store.get("developer_website")),
        ("developer phone", store.get("developer_phone")),
        ("developer email", store.get("developer_email")),
    ):
        text = str(value or "")
        if not text:
            rep.fail(f"Store listing {label} is empty")
        elif text.startswith("PENDING_"):
            rep.fail(f"Store listing {label} is still the placeholder {text!r}")

    aab = _find_aab(app_dir)
    if aab is None:
        rep.fail(f"No .aab in {app_dir / 'artifacts'} — run the generator first")

    # Graphics: Play requires icon, feature graphic, >=2 phone shots,
    # >=4 of each tablet size.
    graphics = listing.get("graphics", {}) or {}
    minimums = {
        "phone_screenshots": 2,
        "tablet_7inch_screenshots": 4,
        "tablet_10inch_screenshots": 4,
    }
    for key_name, image_type, is_list in IMAGE_TYPES:
        entries = graphics.get(key_name) or ([] if is_list else "")
        paths = [app_dir / p for p in (entries if is_list else [entries] if entries else [])]
        present = [p for p in paths if p.is_file()]
        missing = [p for p in paths if not p.is_file()]
        for p in missing:
            rep.fail(f"Missing graphic: {p}")
        need = minimums.get(key_name, 1)
        if len(present) < need:
            rep.fail(f"{image_type}: {len(present)} present, Play requires at least {need}")

    _run_policy_gate(cn, rep)
    return rep


def _run_policy_gate(company_number: str, rep: "Report") -> None:
    """
    Fold apps/generator/policy_gate findings into this preflight.

    The gate covers Play policy risk — listing quality, Data Safety
    consistency, target SDK, permissions, repetitive-content — which is
    separate from the mechanical "can this upload succeed" checks above. A
    blocker there stops the publish; a warning is surfaced but does not.

    A gate that cannot run is itself a blocker: silently publishing because
    the compliance check failed to import is the wrong default.
    """
    try:
        from apps.generator.catalog import Company
        from apps.generator.policy_gate import evaluate_company, evaluate_template
    except Exception as exc:  # noqa: BLE001
        rep.fail(f"Policy gate unavailable ({type(exc).__name__}: {exc})")
        return

    try:
        # Template-level checks matter per publish too: a wrong target SDK, a
        # declared permission or committed signing secrets are not batch-only
        # problems. The catalog-wide checks need the whole company set and stay
        # in the CLI.
        findings = evaluate_template()
        findings.extend(evaluate_company(
            Company(company_number=company_number, company_name="", sic_codes="",
                    domain="", support_email="", archetype=""),
            require_aab=True,
        ))
    except Exception as exc:  # noqa: BLE001
        rep.fail(f"Policy gate errored ({type(exc).__name__}: {exc})")
        return

    for finding in findings:
        text = f"policy gate [{finding.code}] {finding.message}"
        if finding.severity == "blocker":
            rep.fail(text)
        else:
            rep.warn(text)


# ── Publish ─────────────────────────────────────────────────────────────────
def _client(company_number: str):
    """Build an authenticated androidpublisher service for this company."""
    from google.oauth2 import service_account          # google-auth
    from googleapiclient.discovery import build        # google-api-python-client

    key = _accounts_dir() / f"{company_number}.json"
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=[SCOPE],
    )
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)


def publish(
    company_number: str,
    track: str = "internal",
    dry_run: bool = True,
    replace_images: bool = True,
) -> Report:
    """
    Upload the AAB, write the listing and graphics, stage a release on `track`,
    and validate the edit.

    A dry run is NOT an offline check. It needs a working service account and
    an app that already exists in Play Console, and it performs the whole
    sequence for real: it opens a draft edit, uploads the AAB, sends the store
    listing text, replaces the graphics and stages the release on `track`.
    Only the final commit is skipped — the edit is validated by Play and then
    deleted, so none of it is applied and nothing goes live.

    What a dry run still costs: bandwidth for the full bundle, Play API quota,
    and most likely a version code. Google's own description of the upload is
    that it "puts the APK in a storage area, so it can be assigned to a track
    in this or a subsequent edit" — that storage outlives the edit, and the
    published docs do not say a deleted edit releases the code. Treat a
    version code spent on a dry run as spent.

    Pass dry_run=False to commit and release.
    """
    # Preflight first. Importing the Google client up here meant a machine
    # without it reported "No module named googleapiclient" and nothing else,
    # hiding the listing and artifact problems preflight had already found —
    # which are the ones worth fixing before anyone installs anything.
    rep = preflight(company_number)
    rep.dry_run = dry_run
    rep.track = track
    if not rep.ok:
        write_receipt(company_number, rep)
        return rep

    try:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        rep.fail(
            "google-api-python-client is not installed, so nothing can be sent to "
            f"Play from this machine ({exc}). Install it with: "
            "pip install -r requirements.txt"
        )
        write_receipt(company_number, rep)
        return rep

    if track not in VALID_TRACKS:
        return rep.fail(f"Unknown track {track!r}; expected one of {sorted(VALID_TRACKS)}")

    cn = rep.company_number
    app_dir = _app_dir(cn)
    assert app_dir is not None  # preflight guarantees this
    listing = _load_listing(app_dir) or {}
    identity = listing.get("app_identity", {}) or {}
    store = listing.get("store_listing", {}) or {}
    graphics = listing.get("graphics", {}) or {}
    release = listing.get("release", {}) or {}
    pkg = rep.package_name
    language = _lang(listing)

    service = _client(cn)
    edits = service.edits()
    edit_id = ""

    try:
        edit_id = edits.insert(body={}, packageName=pkg).execute()["id"]
        rep.edit_id = edit_id

        # 1. AAB — resumable, these run to tens of MB.
        aab = _find_aab(app_dir)
        assert aab is not None  # preflight guarantees this
        media = MediaFileUpload(
            str(aab), mimetype="application/octet-stream", resumable=True,
        )
        bundle = edits.bundles().upload(
            editId=edit_id, packageName=pkg, media_body=media,
        ).execute()
        rep.version_code = int(bundle["versionCode"])
        logger.info("[%s] uploaded %s as versionCode %s", cn, aab.name, rep.version_code)

        # 2. Store listing.
        edits.listings().update(
            editId=edit_id,
            packageName=pkg,
            language=language,
            body={
                "language": language,
                "title": identity.get("app_name", ""),
                "shortDescription": store.get("short_description", ""),
                "fullDescription": store.get("full_description", ""),
            },
        ).execute()
        logger.info("[%s] wrote %s store listing", cn, language)

        # 3. Graphics.
        for key_name, image_type, is_list in IMAGE_TYPES:
            entries = graphics.get(key_name) or ([] if is_list else "")
            paths = [app_dir / p for p in (entries if is_list else [entries] if entries else [])]
            paths = [p for p in paths if p.is_file()]
            if not paths:
                continue
            if replace_images:
                edits.images().deleteall(
                    editId=edit_id, packageName=pkg,
                    language=language, imageType=image_type,
                ).execute()
            for path in paths:
                edits.images().upload(
                    editId=edit_id, packageName=pkg,
                    language=language, imageType=image_type,
                    media_body=MediaFileUpload(str(path), mimetype="image/png"),
                ).execute()
            rep.uploaded_images[image_type] = len(paths)
            logger.info("[%s] uploaded %d %s", cn, len(paths), image_type)

        # 4. Track + release notes.
        notes = release.get("release_notes") or ""
        track_body: dict = {
            "track": track,
            "releases": [{
                "versionCodes": [str(rep.version_code)],
                "status": "completed",
                **({"name": release["release_name"]} if release.get("release_name") else {}),
                **({"releaseNotes": [{"language": language, "text": notes}]} if notes else {}),
            }],
        }
        edits.tracks().update(
            editId=edit_id, packageName=pkg, track=track, body=track_body,
        ).execute()
        logger.info("[%s] staged versionCode %s on %s", cn, rep.version_code, track)

        # 5. Validate, then commit only if this is for real.
        edits.validate(editId=edit_id, packageName=pkg).execute()
        if dry_run:
            edits.delete(editId=edit_id, packageName=pkg).execute()
            rep.warn(
                "dry run: the AAB, listing and graphics were uploaded to a draft "
                "edit, Play validated it, and the edit was discarded. Nothing was "
                f"released. Version code {rep.version_code} should be treated as "
                "spent — reuse is not guaranteed after an edit is deleted."
            )
        else:
            edits.commit(editId=edit_id, packageName=pkg).execute()
            rep.committed = True
            logger.info("[%s] committed edit %s", cn, edit_id)

    except HttpError as exc:
        detail = exc.content.decode("utf-8", "replace") if exc.content else str(exc)
        rep.fail(f"Play API error {exc.resp.status}: {detail}")
        if exc.resp.status in (403, 404):
            rep.warn(
                "403/404 on a new app usually means the first release has not "
                "been made manually yet, or the service account lacks release "
                "permission for this package."
            )
        _discard(edits, edit_id, pkg, cn)
    except Exception as exc:  # noqa: BLE001 - always clean up the server-side edit
        rep.fail(f"{type(exc).__name__}: {exc}")
        _discard(edits, edit_id, pkg, cn)

    write_receipt(cn, rep)
    return rep


# ── Receipt ─────────────────────────────────────────────────────────────────
# publish() used to leave nothing behind, so afterwards there was no way to
# answer "has this app been published, and as what?" — not from the filesystem
# and not from the row. The receipt is that answer, and it is also the
# evidence: which edit, which version code, which track, committed or not.

RECEIPT_NAME = "publish_receipt.json"


def receipt_path(company_number: str) -> Path | None:
    app_dir = _app_dir(company_number)
    return (app_dir / RECEIPT_NAME) if app_dir else None


def write_receipt(company_number: str, rep: "Report") -> Path | None:
    path = receipt_path(company_number)
    if path is None:
        return None
    payload = rep.as_dict()
    payload["at"] = datetime.now(timezone.utc).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("[%s] could not write %s: %s", company_number, RECEIPT_NAME, e)
        return None
    return path


def read_receipt(company_number: str) -> dict | None:
    """The last publish attempt, or None. A dry run leaves one too."""
    path = receipt_path(company_number)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_published(company_number: str) -> bool:
    """
    True only for a committed release.

    A dry run writes a receipt too — that is the point, it records that the
    edit validated — so the committed flag is what separates "this would
    work" from "this is live".
    """
    rec = read_receipt(company_number)
    return bool(rec and rec.get("committed"))


# ── Data Safety ─────────────────────────────────────────────────────────────
# POST applications/{packageName}/dataSafety takes `safetyLabels`: the contents
# of a Data Safety CSV, in the format Play Console's "Export to CSV" produces.
#
# The question IDs in that CSV are issued by Play Console, so we never
# synthesise one. Export the template once from App content > Data safety >
# Export to CSV, answer it for the zero-data profile, and save it as the
# canonical template. Every generated app shares the same declaration, so the
# same file uploads to all of them.
DS_COL_QUESTION = "Question ID (machine readable)"
DS_COL_RESPONSE = "Response (machine readable)"
DS_COL_VALUE = "Response value"
DS_COL_REQUIREMENT = "Answer requirement"
DS_COLUMNS = (DS_COL_QUESTION, DS_COL_RESPONSE, DS_COL_VALUE, DS_COL_REQUIREMENT)


def _default_ds_template() -> Path:
    return Path(
        os.getenv("PLAY_DATA_SAFETY_TEMPLATE")
        or Path(__file__).parent / "apps" / "generator" / "data_safety_template.csv"
    )


def validate_data_safety_csv(csv_text: str) -> tuple[list[str], list[str]]:
    """
    Structurally check an exported Data Safety CSV before sending it.

    Uses the sheet's own "Answer requirement" column, so it catches a
    half-filled export without us hard-coding any question IDs.

    Returns (errors, warnings).
    """
    import csv as _csv
    from io import StringIO

    errors: list[str] = []
    warnings: list[str] = []

    rows = list(_csv.DictReader(StringIO(csv_text)))
    if not rows:
        return ["Data Safety CSV has no rows"], warnings

    missing_cols = [c for c in DS_COLUMNS if c not in (rows[0].keys() or {})]
    if missing_cols:
        return [f"Data Safety CSV is missing column(s): {missing_cols}"], warnings

    # One question spans several rows when it offers choices.
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault((row.get(DS_COL_QUESTION) or "").strip(), []).append(row)
    groups.pop("", None)

    for question, grp in groups.items():
        requirements = {(r.get(DS_COL_REQUIREMENT) or "").strip().upper() for r in grp}
        values = [(r.get(DS_COL_VALUE) or "").strip().upper() for r in grp]
        trues = sum(1 for v in values if v == "TRUE")
        answered = sum(1 for v in values if v)

        for v in values:
            if v and v not in {"TRUE", "FALSE"}:
                errors.append(f"{question}: response value {v!r} is not TRUE, FALSE or blank")

        if "SINGLE_CHOICE" in requirements and trues != 1:
            errors.append(f"{question}: SINGLE_CHOICE needs exactly one TRUE, found {trues}")
        elif "MULTIPLE_CHOICE" in requirements and trues < 1:
            errors.append(f"{question}: MULTIPLE_CHOICE needs at least one TRUE, found none")
        elif "REQUIRED" in requirements and answered == 0:
            errors.append(f"{question}: REQUIRED but left blank")
        elif "MAYBE_REQUIRED" in requirements and answered == 0:
            warnings.append(
                f"{question}: MAYBE_REQUIRED and blank — fine unless an earlier "
                f"answer makes it mandatory"
            )

    return errors, warnings


def upload_data_safety(
    company_number: str,
    template: str | os.PathLike | None = None,
    dry_run: bool = True,
) -> Report:
    """
    Send this app's Data Safety declaration.

    `template` defaults to apps/generator/data_safety_template.csv (override
    with PLAY_DATA_SAFETY_TEMPLATE). With dry_run=True the CSV is validated
    and nothing is sent — that path needs no credentials and no Google client
    libraries, so it runs anywhere.
    """
    cn = str(company_number).zfill(8)
    rep = Report(company_number=cn, dry_run=dry_run)

    app_dir = _app_dir(cn)
    if app_dir is None or not app_dir.is_dir():
        return rep.fail(f"No generated app directory for company {cn}")

    listing = _load_listing(app_dir)
    if listing is None:
        return rep.fail(f"Missing or unreadable {_expected_listing_path(app_dir)} "
                        f"— run the app stage to generate it")
    rep.package_name = (listing.get("app_identity", {}) or {}).get("package_name", "")
    if not rep.package_name:
        return rep.fail("play_listing.json has no app_identity.package_name")

    key = _accounts_dir() / f"{cn}.json"
    if not key.is_file():
        rep.fail(f"Missing Play service account key: {key}")

    path = Path(template) if template else _default_ds_template()
    if not path.is_file():
        return rep.fail(
            f"No Data Safety CSV at {path}. Export one from Play Console "
            f"(App content > Data safety > Export to CSV), answer it, and save "
            f"it there."
        )
    try:
        csv_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return rep.fail(f"Cannot read {path}: {exc}")

    errors, warnings = validate_data_safety_csv(csv_text)
    for message in errors:
        rep.fail(message)
    for message in warnings:
        rep.warn(message)

    # The generator's own declaration is the source of truth for what this app
    # does; flag any drift from the zero-data profile the template encodes.
    declared = _load_json(_data_safety_path(app_dir)) or {}
    if declared.get("collects_user_data") or declared.get("shares_user_data"):
        rep.warn(
            "data_safety.json says this app collects or shares user data, but "
            f"{path.name} is the shared zero-data declaration — confirm the CSV "
            f"matches this app before sending"
        )

    if not rep.ok:
        return rep

    if dry_run:
        rep.warn(f"dry_run: {path.name} validated, nothing sent")
        return rep

    from googleapiclient.errors import HttpError

    try:
        _client(cn).applications().dataSafety(
            packageName=rep.package_name, body={"safetyLabels": csv_text},
        ).execute()
        rep.committed = True
        logger.info("[%s] uploaded Data Safety declaration for %s", cn, rep.package_name)
    except HttpError as exc:
        detail = exc.content.decode("utf-8", "replace") if exc.content else str(exc)
        rep.fail(f"Play API error {exc.resp.status}: {detail}")
    except Exception as exc:  # noqa: BLE001
        rep.fail(f"{type(exc).__name__}: {exc}")

    return rep


def _discard(edits, edit_id: str, pkg: str, cn: str) -> None:
    """Best-effort cleanup so a failed run doesn't leave a dangling edit."""
    if not edit_id:
        return
    try:
        edits.delete(editId=edit_id, packageName=pkg).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] could not discard edit %s: %s", cn, edit_id, exc)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Publish a generated app to Google Play.")
    parser.add_argument("company_number")
    parser.add_argument("--track", default="internal", choices=sorted(VALID_TRACKS))
    parser.add_argument("--commit", action="store_true",
                        help="actually publish (default is a validate-only dry run)")
    parser.add_argument("--preflight-only", action="store_true",
                        help="check local artefacts only, no network calls")
    parser.add_argument("--data-safety", action="store_true",
                        help="send the Data Safety declaration instead of publishing")
    parser.add_argument("--template",
                        help="Data Safety CSV to use (default: apps/generator/data_safety_template.csv)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.data_safety:
        result = upload_data_safety(
            args.company_number, template=args.template, dry_run=not args.commit,
        )
    elif args.preflight_only:
        result = preflight(args.company_number)
    else:
        result = publish(args.company_number, track=args.track, dry_run=not args.commit)
    print(json.dumps(result.as_dict(), indent=2))
    raise SystemExit(0 if result.ok else 1)
