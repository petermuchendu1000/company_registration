"""Verify APKs produced by the app factory.

The verifier checks package metadata with aapt2 and signatures with apksigner.
It intentionally does not interact with Google Play.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

SDK_DIR_DEFAULT = os.environ.get("ANDROID_HOME", r"C:\Users\LENOVO\AppData\Local\Android\Sdk")


def _latest_build_tools(sdk_dir: str) -> str:
    build_tools_dir = os.path.join(sdk_dir, "build-tools")
    if not os.path.isdir(build_tools_dir):
        raise FileNotFoundError(f"missing {build_tools_dir}")
    versions = sorted(os.listdir(build_tools_dir))
    if not versions:
        raise FileNotFoundError(f"no build-tools installed under {build_tools_dir}")
    return os.path.join(build_tools_dir, versions[-1])


def verify_apk(apk: str, sdk_dir: str = SDK_DIR_DEFAULT) -> dict[str, str]:
    build_tools = _latest_build_tools(sdk_dir)
    aapt2 = os.path.join(build_tools, "aapt2.exe")
    apksigner = os.path.join(build_tools, "apksigner.bat")

    out: dict[str, str] = {"apk": apk, "size_kb": str(os.path.getsize(apk) // 1024)}

    missing_tools = [path for path in [aapt2, apksigner] if not os.path.exists(path)]
    if missing_tools:
        out["error"] = "missing tools: " + ", ".join(missing_tools)
        return out

    badging = subprocess.run(
        [aapt2, "dump", "badging", apk],
        capture_output=True,
        text=True,
    )
    if badging.returncode != 0:
        out["error"] = f"aapt2 failed: {badging.stderr.strip()}"
        return out
    for line in badging.stdout.splitlines():
        if line.startswith("package:"):
            for kv in line[len("package: "):].split(" "):
                if kv.startswith("name="):
                    out["package"] = kv.split("=", 1)[1].strip("'")
                elif kv.startswith("versionName="):
                    out["version"] = kv.split("=", 1)[1].strip("'")
                elif kv.startswith("versionCode="):
                    out["version_code"] = kv.split("=", 1)[1].strip("'")
        elif line.startswith("application-label:"):
            out["label"] = line.split(":", 1)[1].strip().strip("'")
        elif line.startswith("sdkVersion:"):
            out["min_sdk"] = line.split(":", 1)[1].strip().strip("'")
        elif line.startswith("targetSdkVersion:"):
            out["target_sdk"] = line.split(":", 1)[1].strip().strip("'")

    sig = subprocess.run(
        [apksigner, "verify", "--print-certs", apk],
        capture_output=True,
        text=True,
    )
    if sig.returncode != 0:
        out["signed"] = "NO"
        out["sig_error"] = sig.stderr.strip().splitlines()[-1] if sig.stderr else ""
        return out
    out["signed"] = "YES"
    for line in sig.stdout.splitlines():
        if "SHA-256 digest" in line and "signer" in line:
            out["cert_sha256"] = line.split(":", 1)[1].strip()
            break
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify generated APKs")
    parser.add_argument("apks", nargs="*", help="Specific APKs to verify")
    parser.add_argument("--sdk", default=SDK_DIR_DEFAULT)
    args = parser.parse_args(argv)

    if args.apks:
        apks = args.apks
    else:
        root = Path(__file__).resolve().parents[2] / "pipeline_output" / "apps"
        apks = sorted(glob.glob(str(root / "*" / "*.apk")))
    if not apks:
        print("No APKs found.")
        return 2

    try:
        _latest_build_tools(args.sdk)
    except FileNotFoundError as exc:
        print(f"Cannot verify APKs: {exc}")
        print("Install Android SDK build-tools or pass --sdk to a valid SDK directory.")
        return 3

    print(f"{'Package':<30} {'Label':<32} {'Ver':<6} {'Code':<5} {'Min':<4} {'Target':<6} {'Size':<7} Signed")
    print("-" * 116)
    ok = 0
    for apk in apks:
        result = verify_apk(apk, sdk_dir=args.sdk)
        if result.get("signed") != "YES":
            print(f"  !! {os.path.basename(apk)} error={result.get('error') or result.get('sig_error')}")
            continue
        ok += 1
        print(
            f"{result.get('package',''):<30} "
            f"{result.get('label',''):<32} "
            f"{result.get('version',''):<6} "
            f"{result.get('version_code',''):<5} "
            f"{result.get('min_sdk',''):<4} "
            f"{result.get('target_sdk',''):<6} "
            f"{result.get('size_kb','') + 'k':<7} "
            f"{result.get('signed','')}"
        )
    print(f"\n{ok}/{len(apks)} APKs verified")
    return 0 if ok == len(apks) else 1


if __name__ == "__main__":
    sys.exit(main())
