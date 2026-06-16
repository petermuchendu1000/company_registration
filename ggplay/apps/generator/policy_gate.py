"""Google Play policy risk gate for generated apps.

This is a local compliance gate. It does not interact with Google Play and it
cannot guarantee approval. Its job is to fail closed on known rejection risks
before any human submits an app for review.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .catalog import Company, load_companies
from .functionality_catalog import BASE_MODULES

ROOT = Path(__file__).resolve().parents[2]
APP_TEMPLATE = ROOT / "apps" / "template-shift-journal" / "app"
OUTPUT_DIR = ROOT / "pipeline_output" / "apps"


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    source: str

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source": self.source,
        }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_invalid_json": True}


def _latest(path: Path, pattern: str) -> Path | None:
    matches = sorted(path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _manifest_permissions() -> list[str]:
    manifest = APP_TEMPLATE / "src" / "main" / "AndroidManifest.xml"
    if not manifest.exists():
        return []
    text = manifest.read_text(encoding="utf-8")
    return re.findall(r"<uses-permission[^>]+android:name=\"([^\"]+)\"", text)


def _build_gradle_text() -> str:
    path = APP_TEMPLATE / "build.gradle.kts"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _template_screen_count() -> int:
    main = APP_TEMPLATE / "src" / "main" / "java" / "uk" / "template" / "shift" / "MainActivity.kt"
    if not main.exists():
        return 0
    text = main.read_text(encoding="utf-8")
    return len(re.findall(r"@Composable\s+private fun \w+Screen\(", text))


def _has_meaningful_template_functionality() -> bool:
    main = APP_TEMPLATE / "src" / "main" / "java" / "uk" / "template" / "shift" / "MainActivity.kt"
    if not main.exists():
        return False
    text = main.read_text(encoding="utf-8")
    required = [
        "OutlinedTextField",
        "Recent activity",
        "Local note",
        "rememberSaveable",
        "MODULE_TITLES",
        "MODULE_PRIMARY_ACTIONS",
    ]
    return all(token in text for token in required)


def _target_sdk() -> int | None:
    match = re.search(r"targetSdk\s*=\s*(\d+)", _build_gradle_text())
    return int(match.group(1)) if match else None


def _compile_sdk() -> int | None:
    match = re.search(r"compileSdk\s*=\s*(\d+)", _build_gradle_text())
    return int(match.group(1)) if match else None


def _bad_title(title: str) -> bool:
    if not title or len(title) > 30:
        return True
    if re.search(r"[\U0001F300-\U0001FAFF]", title):
        return True
    if re.search(r"([!?.])\1{1,}", title):
        return True
    return False


def _short_description_ok(value: str) -> bool:
    return bool(value and 20 <= len(value) <= 80 and not value.endswith("..."))


def _full_description_ok(value: str) -> bool:
    return bool(value and 500 <= len(value) <= 4000 and "Privacy:" in value and "Key features:" in value)


def _artifact_is_zip(path: Path | None) -> bool:
    return bool(path and path.exists() and zipfile.is_zipfile(path))


def evaluate_company(company: Company, require_aab: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    out_dir = OUTPUT_DIR / company.company_number
    manifest = _read_json(out_dir / "manifest.json")
    listing = _read_json(out_dir / "play_listing.json")
    data_safety = _read_json(out_dir / "data_safety.json")
    apk = _latest(out_dir, "*.apk")
    aab = _latest(out_dir, "*.aab")

    source = company.company_number
    if not out_dir.exists():
        findings.append(Finding("blocker", "missing_output", "No generated app output folder exists.", source))
        return findings
    if not manifest:
        findings.append(Finding("blocker", "missing_manifest", "manifest.json is missing.", source))
    if not listing:
        findings.append(Finding("blocker", "missing_listing", "play_listing.json is missing.", source))
    if not data_safety:
        findings.append(Finding("blocker", "missing_data_safety", "data_safety.json is missing.", source))

    app_identity = listing.get("app_identity", {}) if listing else {}
    store_listing = listing.get("store_listing", {}) if listing else {}
    app_name = app_identity.get("app_name") or manifest.get("display_name") or ""
    if _bad_title(app_name):
        findings.append(Finding("blocker", "metadata_title", "App title is missing, too long, or improperly formatted.", source))

    short = store_listing.get("short_description", "")
    if not _short_description_ok(short):
        findings.append(Finding("blocker", "metadata_short_description", "Short description must be clear and 80 characters or less.", source))

    full = store_listing.get("full_description", "")
    if not _full_description_ok(full):
        findings.append(Finding("blocker", "metadata_full_description", "Full description is too thin or lacks features/privacy detail.", source))

    privacy_url = store_listing.get("privacy_policy_url", "")
    if not privacy_url.startswith("https://") or "PENDING_" in privacy_url:
        findings.append(Finding("blocker", "privacy_policy_url", "Privacy policy must be a live public HTTPS URL, not a placeholder.", source))

    developer_email = store_listing.get("developer_email", "")
    if "@gmail." in developer_email.lower() or not developer_email:
        findings.append(Finding("blocker", "developer_email", "Developer email should be dev@registered-domain, not a Gmail placeholder.", source))

    developer_phone = store_listing.get("developer_phone", "")
    if not developer_phone or "PENDING_" in developer_phone:
        findings.append(Finding("blocker", "developer_phone", "Developer phone is missing or still a placeholder.", source))

    selected_modules = (listing.get("release", {}) or {}).get("selected_modules", []) if listing else []
    if len(selected_modules) < 5:
        findings.append(Finding("blocker", "module_count", "App must have at least five selected screens/functions.", source))

    if not apk:
        findings.append(Finding("blocker", "missing_apk", "No APK exists for local install testing.", source))
    elif not _artifact_is_zip(apk):
        findings.append(Finding("blocker", "invalid_apk", f"APK is not a valid ZIP artifact: {apk.name}", source))

    if require_aab:
        if not aab:
            findings.append(Finding("blocker", "missing_aab", "New Play apps require an Android App Bundle for publishing.", source))
        elif not _artifact_is_zip(aab):
            findings.append(Finding("blocker", "invalid_aab", f"AAB is not a valid ZIP artifact: {aab.name}", source))

    for asset in ["icon-512.png", "feature-graphic.png", "phone-screenshot-1.png", "phone-screenshot-2.png"]:
        if not (out_dir / asset).exists():
            findings.append(Finding("blocker", f"missing_{asset}", f"Required listing asset is missing: {asset}", source))

    if data_safety:
        if data_safety.get("collects_user_data") is not False or data_safety.get("shares_user_data") is not False:
            findings.append(Finding("blocker", "data_safety_claim", "Data safety must match the zero-collection app design.", source))

    return findings


def evaluate_global(companies: list[Company], allow_repetitive_factory: bool = False) -> list[Finding]:
    findings: list[Finding] = []

    target_sdk = _target_sdk()
    if target_sdk is None or target_sdk < 35:
        findings.append(Finding("blocker", "target_sdk", "New apps must target API 35 or higher for current Play submission rules.", "template"))

    compile_sdk = _compile_sdk()
    if compile_sdk is None or compile_sdk < 35:
        findings.append(Finding("blocker", "compile_sdk", "compileSdk should be 35 or higher.", "template"))

    permissions = _manifest_permissions()
    if permissions:
        findings.append(Finding("blocker", "unexpected_permissions", f"Unexpected Android permissions declared: {permissions}", "template"))

    screen_count = _template_screen_count()
    if screen_count < 1:
        findings.append(Finding("blocker", "screen_count", "No Compose screen detected.", "template"))

    if len(BASE_MODULES) < 10:
        findings.append(Finding("blocker", "module_catalog", "Mutation catalog must contain at least 10 screen/function modules.", "catalog"))

    if not _has_meaningful_template_functionality():
        findings.append(Finding("blocker", "minimum_functionality", "Template lacks enough interactive journal functionality.", "template"))

    descriptions = []
    for company in companies:
        listing = _read_json(OUTPUT_DIR / company.company_number / "play_listing.json")
        descriptions.append((listing.get("store_listing", {}) or {}).get("full_description", ""))
    unique_descriptions = {d for d in descriptions if d}
    if len(companies) > 2 and not allow_repetitive_factory:
        findings.append(
            Finding(
                "blocker",
                "repetitive_app_factory",
                "Bulk white-label generation is a spam/repetitive-content risk. Prove distinct app value before scaling past two apps.",
                "catalog",
            )
        )
    if len(unique_descriptions) < len([d for d in descriptions if d]):
        findings.append(Finding("blocker", "duplicate_descriptions", "Two or more apps have identical full descriptions.", "catalog"))

    return findings


def write_report(findings: list[Finding], companies: list[Company]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "play_policy_gate_report.json"
    payload = {
        "company_count": len(companies),
        "blockers": sum(1 for f in findings if f.severity == "blocker"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "findings": [finding.as_dict() for finding in findings],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Google Play policy risk gate")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--allow-missing-aab", action="store_true", help="Use only while Android build-tools are unavailable")
    parser.add_argument("--allow-repetitive-factory", action="store_true", help="Do not use for Play submission readiness")
    args = parser.parse_args(argv)

    all_companies = load_companies(limit=None)
    companies = all_companies[args.offset:args.offset + args.count]
    findings = evaluate_global(companies, allow_repetitive_factory=args.allow_repetitive_factory)
    for company in companies:
        findings.extend(evaluate_company(company, require_aab=not args.allow_missing_aab))

    report = write_report(findings, companies)
    blockers = [finding for finding in findings if finding.severity == "blocker"]
    warnings = [finding for finding in findings if finding.severity == "warning"]

    print(f"Policy gate companies: {len(companies)}")
    print(f"Blockers: {len(blockers)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Report: {report}")
    for finding in findings:
        print(f"{finding.severity.upper()} {finding.source} {finding.code}: {finding.message}")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
