"""Audit generated app artifacts against the company catalog.

This produces a machine-readable readiness report without talking to Google.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .assets import sha256_file
from .catalog import Company, load_companies

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "pipeline_output" / "apps"


def _latest(path: Path, pattern: str) -> Path | None:
    matches = sorted(path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_error": "invalid_json"}


def _slug(value: str, fallback: str) -> str:
    slug = value.lower().replace(" ", "-")
    slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-")
    return slug[:40].strip("-") or fallback


def audit_company(company: Company, require: str = "both") -> dict:
    out_dir = OUTPUT_DIR / company.company_number
    manifest_path = out_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    apk = _latest(out_dir, "*.apk")
    aab = _latest(out_dir, "*.aab")
    signing = out_dir / "signing.json"
    privacy = out_dir / "privacy.html"
    install = out_dir / "install.html"
    icon = out_dir / "icon-512.png"

    missing: list[str] = []
    warnings: list[str] = []

    if not out_dir.exists():
        missing.append("output_dir")
    if not manifest:
        missing.append("manifest")
    if manifest.get("_error"):
        missing.append("valid_manifest")
    if not install.exists():
        missing.append("install_page")
    if not privacy.exists():
        missing.append("privacy_page")
    if not icon.exists():
        missing.append("icon")
    if require in {"apk", "both"} and not apk:
        missing.append("apk")
    if require in {"aab", "both"} and not aab:
        missing.append("aab")

    if manifest:
        expected_slug = _slug(company.display_name, company.company_number)
        if manifest.get("package") != company.application_id:
            missing.append("manifest_package_match")
        if apk and manifest.get("apk") != apk.name:
            warnings.append("manifest_apk_not_latest")
        if aab and manifest.get("aab") != aab.name:
            warnings.append("manifest_aab_not_latest")
        if apk and not apk.name.startswith(expected_slug):
            warnings.append("apk_filename_uses_previous_display_name")
        if aab and not aab.name.startswith(expected_slug):
            warnings.append("aab_filename_uses_previous_display_name")
        if apk and manifest.get("apk_sha256") and manifest["apk_sha256"] != sha256_file(apk):
            missing.append("apk_sha256_match")
        if aab and manifest.get("aab_sha256") and manifest["aab_sha256"] != sha256_file(aab):
            missing.append("aab_sha256_match")

    if not signing.exists():
        warnings.append("signing_backup_missing")
    if not company.domain:
        warnings.append("domain_missing")
    if "@gmail." in company.support_email.lower():
        warnings.append("support_email_is_gmail")

    ready = not missing
    return {
        "company_number": company.company_number,
        "display_name": company.display_name,
        "application_id": company.application_id,
        "flavor": company.flavor,
        "archetype": company.archetype,
        "domain": company.domain,
        "support_email": company.support_email,
        "apk": str(apk) if apk else "",
        "aab": str(aab) if aab else "",
        "manifest": str(manifest_path) if manifest_path.exists() else "",
        "ready": ready,
        "missing": missing,
        "warnings": warnings,
    }


def write_reports(rows: list[dict]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "app_readiness_report.json"
    csv_path = OUTPUT_DIR / "app_readiness_report.csv"
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    fields = [
        "company_number",
        "display_name",
        "application_id",
        "archetype",
        "support_email",
        "apk",
        "aab",
        "ready",
        "missing",
        "warnings",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row[field]) if isinstance(row[field], list) else row[field] for field in fields})
    return json_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit generated app artifacts")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--require", default="both", choices=["none", "apk", "aab", "both"])
    args = parser.parse_args(argv)

    all_companies = load_companies(limit=None)
    end = None if args.count is None else args.offset + args.count
    companies = all_companies[args.offset:end]
    rows = [audit_company(company, require=args.require) for company in companies]
    json_path, csv_path = write_reports(rows)

    ready = sum(1 for row in rows if row["ready"])
    print(f"Audited {len(rows)} companies")
    print(f"Ready: {ready}/{len(rows)} (require={args.require})")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")

    if ready != len(rows):
        missing_counts: dict[str, int] = {}
        for row in rows:
            for item in row["missing"]:
                missing_counts[item] = missing_counts.get(item, 0) + 1
        print("Missing counts:")
        for key, value in sorted(missing_counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {key}: {value}")
    return 0 if ready == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
