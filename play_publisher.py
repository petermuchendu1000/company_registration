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
    return Path(__file__).parent / "pipeline_output" / "companies"


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
    """pipeline_output/companies/{cn} - {name}/app — name varies, so glob on cn."""
    cn = str(company_number).zfill(8)
    base = _companies_dir()
    if not base.is_dir():
        return None
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child.name == cn or child.name.startswith(f"{cn} - ")):
            return child / "app"
    return None


def _load_listing(app_dir: Path) -> dict | None:
    path = app_dir / "play_listing.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable %s: %s", path, exc)
        return None


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
        return rep.fail(f"Missing or unreadable {app_dir / 'play_listing.json'}")

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

    return rep


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

    With dry_run=True (the default) the edit is validated and then deleted, so
    nothing reaches Google Play. Pass dry_run=False to commit.
    """
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    rep = preflight(company_number)
    rep.dry_run = dry_run
    rep.track = track
    if not rep.ok:
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
            rep.warn("dry_run: edit validated then discarded, nothing published")
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = (
        preflight(args.company_number)
        if args.preflight_only
        else publish(args.company_number, track=args.track, dry_run=not args.commit)
    )
    print(json.dumps(result.as_dict(), indent=2))
    raise SystemExit(0 if result.ok else 1)
