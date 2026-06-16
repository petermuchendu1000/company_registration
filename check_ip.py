"""
Utility: verify current public IP is whitelisted in Namecheap API.

Run standalone:
    python check_ip.py

Or import and call from other scripts:
    from check_ip import ensure_ip_whitelisted
    ensure_ip_whitelisted()   # raises SystemExit with instructions if not whitelisted
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
NS = {"nc": "http://api.namecheap.com/xml.response"}

NAMECHEAP_SETTINGS_URL = (
    "https://ap.www.namecheap.com/settings/tools/apiaccess/"
)


def _load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _update_env_ip(new_ip: str) -> None:
    """Rewrite NAMECHEAP_CLIENT_IP in .env."""
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    pattern = r"^(NAMECHEAP_CLIENT_IP\s*=\s*).*$"
    replacement = f"NAMECHEAP_CLIENT_IP={new_ip}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def get_public_ip() -> str:
    try:
        return requests.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception as e:
        raise RuntimeError(f"Could not determine public IP: {e}")


def test_namecheap_api(ip: str) -> tuple[bool, str]:
    """
    Returns (whitelisted: bool, message: str).
    Makes a lightweight domains.getList call with PageSize=1.
    """
    params = {
        "ApiUser":  os.environ.get("NAMECHEAP_API_USER", ""),
        "ApiKey":   os.environ.get("NAMECHEAP_API_KEY", ""),
        "UserName": os.environ.get("NAMECHEAP_USERNAME", ""),
        "ClientIp": ip,
        "Command":  "namecheap.domains.getList",
        "PageSize": "20",
    }
    try:
        r = requests.get(
            "https://api.namecheap.com/xml.response",
            params=params,
            timeout=15,
        )
        root = ET.fromstring(r.text)
        if root.get("Status") == "ERROR":
            errors = [e.text or "" for e in root.findall(".//nc:Error", NS)]
            msg = "; ".join(errors)
            # Only treat IP-related errors as whitelist failures
            if any(kw in msg.lower() for kw in ("invalid request ip", "ip", "not allowed", "whitelist")):
                return False, msg
            # Any other API error means we connected fine — IP is whitelisted
            return True, "OK"
        return True, "OK"
    except Exception as e:
        return False, str(e)


def ensure_ip_whitelisted(verbose: bool = True) -> str:
    """
    Check public IP, update .env if changed, verify Namecheap whitelist.
    Returns the current public IP on success.
    Prints instructions and raises SystemExit(1) if not whitelisted.
    """
    _load_env()

    current_ip = get_public_ip()
    stored_ip  = os.environ.get("NAMECHEAP_CLIENT_IP", "")

    if current_ip != stored_ip:
        if verbose:
            print(f"  Public IP changed: {stored_ip or '(none)'} → {current_ip}")
        _update_env_ip(current_ip)
        os.environ["NAMECHEAP_CLIENT_IP"] = current_ip
    elif verbose:
        print(f"  Public IP: {current_ip} (unchanged)")

    if verbose:
        print("  Testing Namecheap API access...", end=" ", flush=True)

    whitelisted, msg = test_namecheap_api(current_ip)

    if whitelisted:
        if verbose:
            print("OK")
        return current_ip

    # Not whitelisted — print clear instructions
    print("FAILED")
    print()
    print("=" * 60)
    print("  ACTION REQUIRED: whitelist your IP in Namecheap")
    print("=" * 60)
    print(f"  Your current public IP : {current_ip}")
    print(f"  Namecheap API settings : {NAMECHEAP_SETTINGS_URL}")
    print()
    print("  Steps:")
    print("    1. Open the URL above")
    print("    2. Under 'Whitelisted IPs', add:", current_ip)
    print("    3. Save, then re-run this script")
    print()
    if msg:
        print(f"  API error: {msg}")
    print("=" * 60)
    sys.exit(1)


if __name__ == "__main__":
    print("Checking Namecheap API access...")
    ip = ensure_ip_whitelisted(verbose=True)
    print(f"\nAll good. Namecheap API is accessible from {ip}.")
