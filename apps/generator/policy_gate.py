"""
Google Play policy risk gate for generated apps.

A local compliance gate. It does not talk to Google Play and cannot promise
approval. Its job is to fail closed on known rejection risks before a human
submits anything for review.

Recovered from the pre-tenancy ggplay tree and rebuilt against the current
layout. Three of the original checks referenced a module catalog that only
ever existed in that tree, and two template checks were written against a
different MainActivity; those are corrected here rather than carried over as
false blockers.

    python -m apps.generator.policy_gate --tenant acme
    python -m apps.generator.policy_gate --company 13510663
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import archetypes
from . import sic_map
from .catalog import Company, load_companies

ROOT = Path(__file__).resolve().parents[2]
APP_TEMPLATE = ROOT / "apps" / "template-shift-journal" / "app"

# Play's published limits.
TITLE_MAX = 30
SHORT_MIN, SHORT_MAX = 20, 80
FULL_MIN, FULL_MAX = 500, 4000
MIN_TARGET_SDK = 35


@dataclass(frozen=True)
class Finding:
    severity: str          # "blocker" | "warning"
    code: str
    message: str
    source: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code,
                "message": self.message, "source": self.source}


# ── helpers ─────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_invalid_json": True}


def app_dir_for(company_number: str) -> Path | None:
    """
    This tenant's app directory for a company; the name varies, so the
    lookup globs on the number, but only inside the tenant's own subtree.
    See company_paths.
    """
    import company_paths
    found = company_paths.find_company_dir(company_number)
    if found:
        return found / "app"
    return None


def _first(path: Path, pattern: str) -> Path | None:
    hits = sorted(path.glob(pattern)) if path.is_dir() else []
    return hits[0] if hits else None


def _is_zip(path: Path | None) -> bool:
    return bool(path and path.exists() and zipfile.is_zipfile(path))


def _build_gradle_text() -> str:
    path = APP_TEMPLATE / "build.gradle.kts"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _int_setting(name: str) -> int | None:
    match = re.search(rf"{name}\s*=\s*(\d+)", _build_gradle_text())
    return int(match.group(1)) if match else None


def _manifest_permissions() -> list[str]:
    """Permissions in the SOURCE manifest. The merged manifest, which also
    catches permissions contributed by dependencies, is checked at build time
    by the verify*NoPermissions Gradle task."""
    manifest = APP_TEMPLATE / "src" / "main" / "AndroidManifest.xml"
    if not manifest.exists():
        return []
    return re.findall(
        r"<uses-permission[^>]+android:name=\"([^\"]+)\"",
        manifest.read_text(encoding="utf-8"),
    )


def _source_text(*src_dirs: str) -> str:
    """Concatenate the Kotlin under the given `app/src/<dir>` source sets.

    Scans the whole tree rather than one file: the screens moved out of a
    single MainActivity into core/, screens/ and the per-archetype source
    sets, and a check pinned to one path silently stops seeing the code.
    """
    out: list[str] = []
    for name in src_dirs:
        root = APP_TEMPLATE / "src" / name
        if not root.is_dir():
            continue
        for kt in sorted(root.rglob("*.kt")):
            out.append(kt.read_text(encoding="utf-8"))
    return "\n".join(out)


def _composable_count(*src_dirs: str) -> int:
    return len(re.findall(r"@Composable", _source_text(*src_dirs)))


def _is_interactive(*src_dirs: str) -> bool:
    """
    Minimum-functionality smell test: the app must take input and keep state,
    not just render static text.
    """
    text = _source_text(*src_dirs)
    return all(token in text for token in ("OutlinedTextField", "rememberSaveable", "Button"))


def _tracked_signing_secrets() -> list[str]:
    """
    Signing credentials committed to version control.

    write_flavors_gradle used to bake storePassword/keyPassword straight into
    app/build.gradle.kts, which is tracked. It now writes them to a git-ignored
    keystore.properties and emits signingSecret() calls instead, so this should
    find nothing — the check stays because a literal is easy to reintroduce and
    a password committed once stays in the history even after it is removed.
    """
    gradle_file = APP_TEMPLATE / "build.gradle.kts"
    if not gradle_file.exists():
        return []
    if not re.search(r"(storePassword|keyPassword)\s*=\s*\"[^\"]+\"", gradle_file.read_text(encoding="utf-8")):
        return []
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(gradle_file.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        ).returncode == 0
    except Exception:  # noqa: BLE001 - git absent or not a repo
        tracked = False
    return [str(gradle_file.relative_to(ROOT))] if tracked else []


# ── per-company checks ──────────────────────────────────────────────────────
def evaluate_company(company: Company, require_aab: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    src = company.company_number
    app = app_dir_for(src)

    if app is None or not app.is_dir():
        return [Finding("blocker", "missing_output",
                        "No generated app directory — run the generator first.", src)]

    listing = _read_json(app / "listing" / "play_listing.json")
    safety = _read_json(app / "listing" / "data_safety.json")
    manifest = _read_json(app / "manifest.json")

    if not listing:
        findings.append(Finding("blocker", "missing_listing",
                                "listing/play_listing.json is missing.", src))
    if not safety:
        findings.append(Finding("blocker", "missing_data_safety",
                                "listing/data_safety.json is missing.", src))

    identity = listing.get("app_identity", {}) or {}
    store = listing.get("store_listing", {}) or {}

    title = identity.get("app_name") or manifest.get("display_name") or ""
    if not title:
        findings.append(Finding("blocker", "metadata_title", "App title is missing.", src))
    elif len(title) > TITLE_MAX:
        findings.append(Finding("blocker", "metadata_title",
                                f"Title is {len(title)} chars, Play allows {TITLE_MAX}.", src))
    elif re.search(r"[\U0001F300-\U0001FAFF]", title) or re.search(r"([!?.])\1", title):
        findings.append(Finding("blocker", "metadata_title",
                                "Title uses emoji or repeated punctuation.", src))

    short = store.get("short_description", "")
    if not (short and SHORT_MIN <= len(short) <= SHORT_MAX) or short.endswith("..."):
        findings.append(Finding("blocker", "metadata_short_description",
                                f"Short description must be {SHORT_MIN}-{SHORT_MAX} chars and "
                                f"not truncated (got {len(short)}).", src))

    full = store.get("full_description", "")
    if not full:
        findings.append(Finding("blocker", "metadata_full_description",
                                "Full description is missing.", src))
    elif len(full) > FULL_MAX:
        findings.append(Finding("blocker", "metadata_full_description",
                                f"Full description is {len(full)} chars, Play allows {FULL_MAX}.", src))
    elif len(full) < FULL_MIN:
        findings.append(Finding("warning", "metadata_full_description",
                                f"Full description is only {len(full)} chars; thin listings "
                                f"are a common rejection reason.", src))

    privacy = store.get("privacy_policy_url", "")
    if not privacy.startswith("https://") or "PENDING_" in privacy:
        findings.append(Finding("blocker", "privacy_policy_url",
                                "Privacy policy must be a live public HTTPS URL, not a placeholder.", src))

    dev_email = store.get("developer_email", "")
    if not dev_email or "PENDING_" in dev_email:
        findings.append(Finding("blocker", "developer_email", "Developer email is missing.", src))
    elif "@gmail." in dev_email.lower():
        findings.append(Finding("blocker", "developer_email",
                                "Google requires an organisation address, not a personal Gmail.", src))

    dev_phone = store.get("developer_phone", "")
    if not dev_phone or "PENDING_" in dev_phone:
        findings.append(Finding("blocker", "developer_phone",
                                "Developer phone is missing or still a placeholder.", src))

    artifacts = app / "artifacts"
    apk, aab = _first(artifacts, "*.apk"), _first(artifacts, "*.aab")
    if require_aab:
        if not aab:
            findings.append(Finding("blocker", "missing_aab",
                                    "Play requires an Android App Bundle for new apps.", src))
        elif not _is_zip(aab):
            findings.append(Finding("blocker", "invalid_aab", f"AAB is not a valid archive: {aab.name}", src))
    if apk and not _is_zip(apk):
        findings.append(Finding("blocker", "invalid_apk", f"APK is not a valid archive: {apk.name}", src))
    elif not apk:
        findings.append(Finding("warning", "missing_apk",
                                "No APK for on-device testing before submission.", src))

    graphics = app / "graphics"
    for asset in ("icon-512.png", "feature-graphic.png",
                  "phone-screenshot-1.png", "phone-screenshot-2.png"):
        if not (graphics / asset).exists():
            findings.append(Finding("blocker", f"missing_{asset}",
                                    f"Required listing asset missing: graphics/{asset}", src))

    if safety and not safety.get("_invalid_json"):
        if safety.get("collects_user_data") is not False or safety.get("shares_user_data") is not False:
            findings.append(Finding("blocker", "data_safety_claim",
                                    "Data Safety must match the zero-collection design, or the "
                                    "design must change to match the declaration.", src))

    # What the listing says the app ships, against what the SIC code would
    # build today. A stale listing describes screens the artifact may not have,
    # which is a metadata violation on top of a wrong store page.
    release = listing.get("release", {}) or {}
    modules = release.get("selected_modules", [])
    expected = archetypes.for_sic(company.sic_codes)
    if not modules:
        findings.append(Finding("warning", "module_count",
                                "Listing declares no selected_modules — regenerate assets so the "
                                "store page names the screens this app actually ships.", src))
    elif release.get("archetype") != expected.id:
        findings.append(Finding("blocker", "archetype_mismatch",
                                f"Listing was written for archetype "
                                f"'{release.get('archetype') or 'unknown'}' but this company's SIC "
                                f"code now builds '{expected.id}'. Regenerate before publishing.", src))

    return findings


# ── template / catalog-wide checks ──────────────────────────────────────────
def evaluate_template() -> list[Finding]:
    """
    Checks on the template itself, independent of any company.

    Split out from the catalog-wide checks so a single publish can run them:
    shipping an app whose template targets the wrong SDK, declares a
    permission, or carries committed signing secrets is a per-publish problem,
    not just a batch one.
    """
    findings: list[Finding] = []

    target = _int_setting("targetSdk")
    if target is None or target < MIN_TARGET_SDK:
        findings.append(Finding("blocker", "target_sdk",
                                f"targetSdk must be {MIN_TARGET_SDK}+ for current Play rules "
                                f"(found {target}).", "template"))
    compile_sdk = _int_setting("compileSdk")
    if compile_sdk is None or compile_sdk < MIN_TARGET_SDK:
        findings.append(Finding("blocker", "compile_sdk",
                                f"compileSdk should be {MIN_TARGET_SDK}+ (found {compile_sdk}).", "template"))

    permissions = _manifest_permissions()
    if permissions:
        findings.append(Finding("blocker", "unexpected_permissions",
                                f"Source manifest declares permissions: {permissions}", "template"))

    # Each archetype is a separate product and has to clear the bar on its
    # own: it ships with the shared core, never with another archetype.
    for arch in archetypes.ALL:
        arch_src = arch.src_dir.removeprefix("src/")
        arch_root = APP_TEMPLATE / "src" / arch_src
        label = f"archetype:{arch.id}"

        if not arch_root.is_dir():
            findings.append(Finding("blocker", "missing_archetype_source",
                                    f"No source directory at {arch.src_dir} — the generator "
                                    f"would put an empty source set on the compile path.", label))
            continue
        if "fun activeArchetype()" not in _source_text(arch_src):
            findings.append(Finding("blocker", "missing_archetype_entrypoint",
                                    f"{arch.src_dir} defines no activeArchetype() — MainActivity "
                                    f"will not compile for a flavor using it.", label))

        if _composable_count("main", arch_src) < 1:
            findings.append(Finding("blocker", "screen_count",
                                    "No Composable found in the template.", label))
        if not _is_interactive("main", arch_src):
            findings.append(Finding("blocker", "minimum_functionality",
                                    "Built app has no text input, saved state or buttons — Play "
                                    "rejects apps with minimal functionality.", label))

    for path in _tracked_signing_secrets():
        findings.append(Finding("blocker", "signing_secrets_in_vcs",
                                f"Keystore passwords are committed in {path}. Move signing config "
                                f"to a git-ignored properties file or environment variables.", "template"))

    return findings


def evaluate_catalog(companies: list[Company], allow_repetitive_factory: bool = False) -> list[Finding]:
    """Checks that only make sense across the whole set of apps."""
    findings: list[Finding] = []

    descriptions = []
    for company in companies:
        app = app_dir_for(company.company_number)
        if app:
            listing = _read_json(app / "listing" / "play_listing.json")
            descriptions.append((listing.get("store_listing", {}) or {}).get("full_description", ""))
    present = [d for d in descriptions if d]
    if len(set(present)) < len(present):
        findings.append(Finding("blocker", "duplicate_descriptions",
                                "Two or more apps share an identical full description.", "catalog"))

    # Archetype spread. Play's Spam policy lists "creating multiple apps with
    # highly similar functionality, content, and user experience" as a
    # violation, so what matters is how many apps share a feature set, not how
    # many apps there are. Reported either way; it is the number a human needs
    # in front of them before deciding to scale.
    spread: dict[str, int] = {}
    for company in companies:
        spread[archetypes.for_sic(company.sic_codes).id] = (
            spread.get(archetypes.for_sic(company.sic_codes).id, 0) + 1
        )
    if spread:
        summary = ", ".join(f"{k}×{v}" for k, v in sorted(spread.items()))
        biggest_id, biggest = max(spread.items(), key=lambda kv: kv[1])
        findings.append(Finding("warning", "archetype_spread",
                                f"Archetype distribution: {summary}. "
                                f"{biggest} app(s) share the '{biggest_id}' feature set; apps "
                                f"within one archetype differ only in branding and trade "
                                f"figures.", "catalog"))

    # Two companies on the same archetype whose trade content also matches
    # ship the same screens showing the same numbers, differing only in
    # colour and company name. That is the literal case Play's Spam policy
    # names: "multiple apps with highly similar functionality, content, and
    # user experience". Catching it needs the content compared, not just the
    # archetype counted.
    fingerprints: dict[tuple, list[str]] = {}
    for company in companies:
        content = sic_map.lookup(company.sic_codes)
        key = (
            archetypes.for_sic(company.sic_codes).id,
            content["calc_formula"],
            content["info_title"],
            tuple(item["k"] for item in content["info_items"]),
        )
        fingerprints.setdefault(key, []).append(company.company_number)
    for key, members in sorted(fingerprints.items()):
        if len(members) > 1:
            findings.append(Finding("blocker", "identical_product",
                                    f"{len(members)} apps are the same product with the same "
                                    f"content — archetype '{key[0]}', identical calculator and "
                                    f"reference list: {', '.join(sorted(members))}. They differ "
                                    f"only in branding, which is Play's repetitive-content "
                                    f"violation. Give these trades distinct content or an "
                                    f"archetype of their own.", "catalog"))

    if len(companies) > 2 and not allow_repetitive_factory:
        findings.append(Finding("blocker", "repetitive_app_factory",
                                "Bulk white-label generation is a spam/repetitive-content risk under "
                                "Play's Minimum Functionality policy. Establish distinct per-app value "
                                "before scaling past two apps.", "catalog"))

    return findings


# ── reporting ───────────────────────────────────────────────────────────────
def run_gate(companies: list[Company], require_aab: bool = True,
             allow_repetitive_factory: bool = False) -> list[Finding]:
    findings = evaluate_template()
    findings.extend(evaluate_catalog(companies, allow_repetitive_factory=allow_repetitive_factory))
    for company in companies:
        findings.extend(evaluate_company(company, require_aab=require_aab))
    return findings


def write_report(findings: list[Finding], companies: list[Company]) -> Path:
    # Per tenant: the report names companies, so a shared path would put one
    # tenant's company list where another tenant's run would overwrite it.
    import company_paths
    out_dir = company_paths.tenant_root()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "play_policy_gate_report.json"
    path.write_text(json.dumps({
        "company_count": len(companies),
        "blockers": sum(1 for f in findings if f.severity == "blocker"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "findings": [f.as_dict() for f in findings],
    }, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Google Play policy risk gate")
    parser.add_argument("--tenant", metavar="SLUG", help="Tenant whose companies to check")
    parser.add_argument("--company", nargs="+", metavar="CN", help="Only these company numbers")
    parser.add_argument("--count", type=int, default=None, help="Check at most this many")
    parser.add_argument("--allow-missing-aab", action="store_true",
                        help="Use only while Android build-tools are unavailable")
    parser.add_argument("--allow-repetitive-factory", action="store_true",
                        help="Do not use for Play submission readiness")
    args = parser.parse_args(argv)

    if args.tenant:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import db
        tenant = db.get_tenant_by_slug(args.tenant)
        if not tenant:
            print(f"No such tenant: {args.tenant}")
            return 2
        db.set_current_tenant(tenant["id"])

    companies = load_companies(limit=args.count, company_numbers=args.company)
    findings = run_gate(companies,
                        require_aab=not args.allow_missing_aab,
                        allow_repetitive_factory=args.allow_repetitive_factory)
    report = write_report(findings, companies)

    blockers = [f for f in findings if f.severity == "blocker"]
    warnings = [f for f in findings if f.severity == "warning"]
    print(f"Companies checked: {len(companies)}")
    print(f"Blockers: {len(blockers)}   Warnings: {len(warnings)}")
    print(f"Report: {report}")
    for f in findings:
        print(f"  {f.severity.upper():<8} {f.source:<10} {f.code}: {f.message}")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
