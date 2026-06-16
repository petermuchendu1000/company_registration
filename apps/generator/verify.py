"""Verify APKs produced by the factory: signature, package id, labels, icon.

Runs apksigner + aapt2 from the Android SDK build-tools and prints a table.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

SDK_DIR = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Android/Sdk"))
BUILD_TOOLS_DIR = os.path.join(SDK_DIR, "build-tools")


def _latest_build_tools() -> str:
    if not os.path.isdir(BUILD_TOOLS_DIR):
        raise FileNotFoundError(f"missing {BUILD_TOOLS_DIR}")
    versions = sorted(os.listdir(BUILD_TOOLS_DIR))
    if not versions:
        raise FileNotFoundError("no build-tools installed")
    return os.path.join(BUILD_TOOLS_DIR, versions[-1])


def verify_apk(apk: str) -> dict[str, str]:
    bt = _latest_build_tools()
    aapt2 = os.path.join(bt, "aapt2.exe")
    apksigner = os.path.join(bt, "apksigner.bat")

    out: dict[str, str] = {"apk": apk, "size_kb": str(os.path.getsize(apk) // 1024)}

    badging = subprocess.run(
        [aapt2, "dump", "badging", apk], capture_output=True, text=True,
    )
    if badging.returncode != 0:
        out["error"] = f"aapt2 failed: {badging.stderr.strip()}"
        return out
    for line in badging.stdout.splitlines():
        if line.startswith("package:"):
            # package: name='uk.c13510663.shift' versionCode='1' versionName='1.0'
            for kv in line[len("package: "):].split(" "):
                if kv.startswith("name="):
                    out["package"] = kv.split("=", 1)[1].strip("'")
                elif kv.startswith("versionName="):
                    out["version"] = kv.split("=", 1)[1].strip("'")
        elif line.startswith("application-label:"):
            out["label"] = line.split(":", 1)[1].strip().strip("'")
        elif line.startswith("sdkVersion:"):
            out["min_sdk"] = line.split(":", 1)[1].strip().strip("'")
        elif line.startswith("targetSdkVersion:"):
            out["target_sdk"] = line.split(":", 1)[1].strip().strip("'")

    sig = subprocess.run(
        [apksigner, "verify", "--print-certs", apk], capture_output=True, text=True,
    )
    if sig.returncode != 0:
        out["signed"] = "NO"
        out["sig_error"] = sig.stderr.strip().splitlines()[-1] if sig.stderr else ""
        return out
    out["signed"] = "YES"
    for line in sig.stdout.splitlines():
        if "SHA-256 digest" in line and "signer" in line:
            out["cert_sha256"] = line.split(":", 1)[1].strip()[:32]
            break
    return out


def main(argv: list[str]) -> int:
    if argv:
        apks = argv
    else:
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "pipeline_output", "apps")
        apks = sorted(glob.glob(os.path.join(root, "*", "*.apk")))
    if not apks:
        print("No APKs found.")
        return 2

    print(f"{'Package':<28}  {'Label':<32}  {'Ver':<6}  {'MinSdk':<6}  {'Size':<7}  Signed")
    print("-" * 100)
    ok = 0
    for apk in apks:
        r = verify_apk(apk)
        if r.get("signed") != "YES":
            print(f"  !! {os.path.basename(apk)}  error={r.get('error') or r.get('sig_error')}")
            continue
        ok += 1
        print(f"{r.get('package',''):<28}  {r.get('label',''):<32}  {r.get('version',''):<6}  "
              f"{r.get('min_sdk',''):<6}  {r.get('size_kb','')+'k':<7}  {r.get('signed','')}  cert={r.get('cert_sha256','')}")
    print(f"\n{ok}/{len(apks)} APKs verified")
    return 0 if ok == len(apks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
