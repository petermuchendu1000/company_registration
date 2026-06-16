"""End-to-end Android app factory.

    python -m apps.generator.generate --count 3

Steps:
  1. Load N companies from pipeline_output/companies_pipeline.xlsx.
  2. For each company, synthesize a palette, mint a keystore, and write a
     per-flavor Android resource overlay.
  3. Rewrite Gradle product flavors and signing configs.
  4. Ensure local.properties points at the Android SDK.
  5. Run Gradle assemble/bundle tasks.
  6. Copy signed APK/AAB artifacts to pipeline_output/apps/{company_number}/.
  7. Generate install assets, checksums, and machine-readable manifests.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from . import assets
from .brand_synth import palette_for
from .catalog import Company, load_companies, summarize
from .flavor_writer import BuiltFlavor, clean_stale_flavors, write_flavor_resources, write_flavors_gradle
from .functionality_catalog import module_payload
from .keystore_mint import mint as mint_keystore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_ROOT = os.path.join(ROOT, "apps", "template-shift-journal")
APP_DIR = os.path.join(TEMPLATE_ROOT, "app")
KEYSTORES_DIR = os.path.join(APP_DIR, "keystores")
OUTPUT_DIR = os.path.join(ROOT, "pipeline_output", "apps")
SUMMARY_PATH = os.path.join(OUTPUT_DIR, "app_factory_summary.json")

SDK_DIR_DEFAULT = os.environ.get(
    "ANDROID_HOME",
    r"C:\Users\LENOVO\AppData\Local\Android\Sdk",
)


@dataclass(frozen=True)
class CollectedArtifacts:
    apk: str | None = None
    aab: str | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: str, fallback: str) -> str:
    slug = value.lower().replace(" ", "-")
    slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-")
    return slug[:40].strip("-") or fallback


def _gradle_task_suffix(flavor: str) -> str:
    return flavor[:1].upper() + flavor[1:]


def _latest_existing(path: Path, pattern: str) -> Path | None:
    matches = sorted(path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def preflight(
    sdk_dir: str,
    require_sdk: bool = True,
    require_build_tools: bool = False,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the local Android build environment."""
    errors: list[str] = []
    warnings: list[str] = []

    gradlew = os.path.join(TEMPLATE_ROOT, "gradlew.bat")
    if not os.path.exists(gradlew):
        errors.append(f"Gradle wrapper missing: {gradlew}")

    if not sdk_dir or not os.path.isdir(sdk_dir):
        msg = f"Android SDK directory missing: {sdk_dir}"
        if require_sdk:
            errors.append(msg)
        else:
            warnings.append(msg)
    else:
        platform = os.path.join(sdk_dir, "platforms", "android-35")
        if not os.path.isdir(platform):
            warnings.append(f"Android platform android-35 not found under {sdk_dir}")
        build_tools = os.path.join(sdk_dir, "build-tools")
        if not os.path.isdir(build_tools) or not os.listdir(build_tools):
            msg = f"Android SDK build-tools missing under {build_tools}"
            if require_build_tools:
                errors.append(msg)
            else:
                warnings.append(msg)

    keytool = shutil.which("keytool") or shutil.which("keytool.exe")
    java_home = os.environ.get("JAVA_HOME", "")
    if not keytool and java_home:
        candidate = os.path.join(java_home, "bin", "keytool.exe")
        if os.path.exists(candidate):
            keytool = candidate
    if not keytool:
        warnings.append("keytool not found on PATH/JAVA_HOME; keystore minting may fail")

    try:
        __import__("qrcode")
    except ModuleNotFoundError:
        warnings.append("Python package qrcode is missing; QR images will be placeholder link cards")

    return errors, warnings


def _ensure_local_properties(sdk_dir: str = SDK_DIR_DEFAULT) -> None:
    path = os.path.join(TEMPLATE_ROOT, "local.properties")
    escaped = sdk_dir.replace("\\", "\\\\").replace(":", "\\:")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"sdk.dir={escaped}\n")


def build_flavors(companies: Iterable[Company]) -> list[BuiltFlavor]:
    companies = list(companies)
    built: list[BuiltFlavor] = []
    total = len(companies)
    for index, company in enumerate(companies, 1):
        print(f"  [{index}/{total}] {company.company_number} {company.display_name}", flush=True)
        palette = palette_for(company.company_name)
        flavor_dir = write_flavor_resources(company, palette, APP_DIR)
        keystore = mint_keystore(company.company_number, company.company_name, KEYSTORES_DIR)
        built.append(BuiltFlavor(company=company, palette=palette, keystore=keystore, flavor_dir=flavor_dir))
    write_flavors_gradle(built, APP_DIR)
    clean_stale_flavors(APP_DIR, keep_flavors={b.company.flavor for b in built})
    return built


def run_gradle(built: list[BuiltFlavor], variant: str = "Release", artifact: str = "both") -> int:
    """Invoke the Gradle wrapper to build all requested flavor artifacts."""
    gradlew = os.path.join(TEMPLATE_ROOT, "gradlew.bat")
    if not os.path.exists(gradlew):
        print(f"  ERROR: {gradlew} not found")
        return 1

    tasks: list[str] = []
    for built_flavor in built:
        suffix = _gradle_task_suffix(built_flavor.company.flavor)
        if artifact in {"apk", "both"}:
            tasks.append(f":app:assemble{suffix}{variant}")
        if artifact in {"aab", "both"}:
            tasks.append(f":app:bundle{suffix}{variant}")

    cmd = [gradlew, "--no-daemon", "--console=plain", "--stacktrace", *tasks]
    print(f"\n  Gradle: {' '.join(tasks)}")
    started = time.time()
    env = os.environ.copy()
    env.setdefault("GRADLE_USER_HOME", os.path.join(ROOT, ".gradle-home"))
    result = subprocess.run(cmd, cwd=TEMPLATE_ROOT, env=env)
    elapsed = time.time() - started
    print(f"  Gradle exit={result.returncode} in {elapsed:.1f}s")
    return result.returncode


def collect_artifacts(
    built: list[BuiltFlavor],
    variant: str = "release",
    artifact: str = "both",
) -> dict[str, CollectedArtifacts]:
    """Copy built APK/AAB files to pipeline_output/apps/{company_number}/."""
    collected: dict[str, CollectedArtifacts] = {}
    for built_flavor in built:
        company = built_flavor.company
        out_dir = os.path.join(OUTPUT_DIR, company.company_number)
        os.makedirs(out_dir, exist_ok=True)
        slug = _slug(company.display_name, company.company_number)
        apk_dst: str | None = None
        aab_dst: str | None = None

        if artifact in {"apk", "both"}:
            apk_src = os.path.join(
                APP_DIR,
                "build",
                "outputs",
                "apk",
                company.flavor,
                variant,
                f"app-{company.flavor}-{variant}.apk",
            )
            if os.path.exists(apk_src):
                apk_dst = os.path.join(out_dir, f"{slug}-{variant}.apk")
                shutil.copy2(apk_src, apk_dst)
                print(f"  [{company.company_number}] APK {apk_dst} ({os.path.getsize(apk_dst) // 1024} KB)")
            else:
                print(f"  [{company.company_number}] MISSING APK {apk_src}")

        if artifact in {"aab", "both"}:
            bundle_dir = f"{company.flavor}{variant}"
            aab_src = os.path.join(
                APP_DIR,
                "build",
                "outputs",
                "bundle",
                bundle_dir,
                f"app-{company.flavor}-{variant}.aab",
            )
            if os.path.exists(aab_src):
                aab_dst = os.path.join(out_dir, f"{slug}-{variant}.aab")
                shutil.copy2(aab_src, aab_dst)
                print(f"  [{company.company_number}] AAB {aab_dst} ({os.path.getsize(aab_dst) // 1024} KB)")
            else:
                print(f"  [{company.company_number}] MISSING AAB {aab_src}")

        collected[company.company_number] = CollectedArtifacts(apk=apk_dst, aab=aab_dst)
    return collected


def backup_and_make_assets(built: list[BuiltFlavor], collected: dict[str, CollectedArtifacts]) -> None:
    """Copy signing material locally and generate per-company metadata assets."""
    for built_flavor in built:
        company = built_flavor.company
        modules = module_payload(company)
        out_dir = Path(ROOT, "pipeline_output", "apps", company.company_number)
        artifacts = collected.get(company.company_number, CollectedArtifacts())
        apk_path = Path(artifacts.apk) if artifacts.apk else _latest_existing(out_dir, "*.apk")
        aab_path = Path(artifacts.aab) if artifacts.aab else _latest_existing(out_dir, "*.aab")

        keystore_path = Path(built_flavor.keystore.path)
        if keystore_path.exists():
            assets.backup_keystore(
                out_dir,
                keystore_path,
                built_flavor.keystore.store_password,
                built_flavor.keystore.key_alias,
            )

        assets.generate_all(
            {
                "display_name": company.display_name,
                "company_name": company.company_name,
                "company_number": company.company_number,
                "archetype": company.archetype,
                "role_noun": modules["role_noun"],
                "role_start": modules["role_start"],
                "role_end": modules["role_end"],
                "export_title": modules["export_title"],
                "modules": modules["modules"],
                "support_email": company.support_email,
                "application_id": company.application_id,
                "domain": company.domain,
                "flavor": company.flavor,
                "developer_name": company.developer_display_name,
                "organization_phone": company.organization_phone,
                "generated_at": utc_now(),
            },
            apk_path if apk_path else None,
            aab_path if aab_path else None,
            out_dir,
            built_flavor.palette.primary,
        )


def _complete(artifacts: CollectedArtifacts, requested: str) -> bool:
    if requested == "apk":
        return bool(artifacts.apk)
    if requested == "aab":
        return bool(artifacts.aab)
    return bool(artifacts.apk and artifacts.aab)


def write_summary(built: list[BuiltFlavor], collected: dict[str, CollectedArtifacts], rc: int) -> None:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    rows = []
    for built_flavor in built:
        company = built_flavor.company
        artifacts = collected.get(company.company_number, CollectedArtifacts())
        if not artifacts.apk or not artifacts.aab:
            out_dir = Path(OUTPUT_DIR, company.company_number)
            artifacts = CollectedArtifacts(
                apk=artifacts.apk or str(_latest_existing(out_dir, "*.apk") or "") or None,
                aab=artifacts.aab or str(_latest_existing(out_dir, "*.aab") or "") or None,
            )
        rows.append(
            {
                "company_number": company.company_number,
                "display_name": company.display_name,
                "application_id": company.application_id,
                "flavor": company.flavor,
                "archetype": company.archetype,
                "support_email": company.support_email,
                "domain": company.domain,
                "apk": artifacts.apk,
                "aab": artifacts.aab,
            }
        )
    payload = {
        "generated_at": utc_now(),
        "return_code": rc,
        "count": len(rows),
        "apps": rows,
    }
    Path(SUMMARY_PATH).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Android app factory")
    parser.add_argument("--count", type=int, default=3, help="Number of companies to build")
    parser.add_argument("--offset", type=int, default=0, help="Zero-based company offset in the workbook")
    parser.add_argument("--variant", default="Release", choices=["Release", "Debug"])
    parser.add_argument("--artifact", default="both", choices=["apk", "aab", "both"])
    parser.add_argument("--sdk", default=SDK_DIR_DEFAULT)
    parser.add_argument("--skip-build", action="store_true", help="Generate flavors but do not run Gradle")
    parser.add_argument("--collect-only", action="store_true", help="Collect existing Gradle outputs without running Gradle")
    parser.add_argument("--no-preflight", action="store_true", help="Skip Android SDK environment checks")
    args = parser.parse_args(argv)

    all_companies = load_companies(limit=None)
    companies = all_companies[args.offset:args.offset + args.count]
    if not companies:
        print("No companies loaded. Bail.")
        return 2

    print("=" * 72)
    print(f"ANDROID APP FACTORY - {len(companies)} companies, variant={args.variant}, artifact={args.artifact}")
    print("=" * 72)
    print(summarize(companies))

    if not args.no_preflight:
        print("\n[0] Preflight")
        errors, warnings = preflight(
            args.sdk,
            require_sdk=not args.skip_build,
            require_build_tools=not args.skip_build,
        )
        for warning in warnings:
            print(f"  WARN: {warning}")
        if errors:
            for error in errors:
                print(f"  ERROR: {error}")
            return 3
        print("  OK: environment checks passed")

    print("\n[1] Writing brand overlays + minting keystores")
    built = build_flavors(companies)
    print(f"  wrote {len(built)} flavor overlays + flavors.gradle.kts + keystores")

    _ensure_local_properties(args.sdk)
    print(f"  local.properties -> sdk.dir={args.sdk}")

    if args.skip_build:
        print("\n[SKIPPED] Gradle build. Generating assets and keystore backups anyway.")
        backup_and_make_assets(built, {})
        write_summary(built, {}, 0)
        return 0

    if args.collect_only:
        print("\n[2] Collecting existing Gradle artifacts")
        collected = collect_artifacts(built, variant=args.variant.lower(), artifact=args.artifact)
        complete = [cn for cn, artifacts in collected.items() if _complete(artifacts, args.artifact)]
        print("\n[3] Backing up keystores and generating install assets")
        backup_and_make_assets(built, collected)
        final_rc = 0 if len(complete) == len(built) else 1
        write_summary(built, collected, final_rc)
        if final_rc:
            print(f"\n  WARN: only {len(complete)}/{len(built)} apps collected all requested artifacts.")
        else:
            print(f"\n  OK: all {len(collected)} apps collected from existing Gradle outputs.")
        return final_rc

    print("\n[2] Running Gradle")
    rc = run_gradle(built, variant=args.variant, artifact=args.artifact)
    if rc != 0:
        print("\nBuild failed. See Gradle output above.")
        write_summary(built, {}, rc)
        return rc

    print("\n[3] Collecting artifacts")
    collected = collect_artifacts(built, variant=args.variant.lower(), artifact=args.artifact)
    complete = [cn for cn, artifacts in collected.items() if _complete(artifacts, args.artifact)]
    if len(complete) != len(built):
        print(f"\n  WARN: only {len(complete)}/{len(built)} apps collected all requested artifacts.")
    else:
        print(f"\n  OK: all {len(collected)} apps in {OUTPUT_DIR}")

    print("\n[4] Backing up keystores and generating install assets")
    backup_and_make_assets(built, collected)
    final_rc = 0 if len(complete) == len(built) else 1
    write_summary(built, collected, final_rc)
    return final_rc


if __name__ == "__main__":
    sys.exit(main())
