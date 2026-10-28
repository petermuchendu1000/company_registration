"""
Utility: verify current public IP is whitelisted in Namecheap API.

Run standalone:
    python check_ip.py

Or import and call from other scripts:
    from check_ip import ensure_ip_whitelisted
    ensure_ip_whitelisted()   # raises SystemExit with instructions if not whitelisted
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
NS = {"nc": "http://api.namecheap.com/xml.response"}

NAMECHEAP_SETTINGS_URL = (
    "https://ap.www.namecheap.com/settings/tools/apiaccess/"
)

# api.ipify.org stays first, so the answer is byte-identical to the previous
# behaviour whenever ipify is up. The rest exist only for when it is not:
# there was one provider and no fallback, so a single third-party outage made
# stage 1 raise and took all five pipeline stages with it, including the four
# that never speak to Namecheap. All four were checked to return a bare
# address.
IP_PROVIDERS = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
)

# Whether a Namecheap error is "your IP is not whitelisted" or something else
# entirely. The previous rule tested the substring "ip", which fires inside
# ordinary English - Descr-ip-tion, Mult-ip-le, Rec-ip-ient, Z-ip, Sh-ip-ping,
# Subscr-ip-tion, Equ-ip-ment, Scr-ip-t - and so misread 10 of 25 sample
# messages as whitelist failures. This matches "ip" as a whole word only, plus
# the two parameter names Namecheap actually uses, and gets all 25 right.
#
# "not allowed" was dropped along with the substring test: no documented
# Namecheap error uses that wording for an IP problem (the access-denial text
# is "API Key is invalid or API access has not been enabled", a credential
# fault), and it was the next most likely source of a false match.
_IP_ERROR_RE = re.compile(
    r"""
      \bip\b                        # "Invalid request IP", "IP is blocked"
    | \b(?:request|client)\s*ip\b   # "Parameter RequestIP is invalid"
    | whitelist                     # "IP is not whitelisted"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Error 1011102, "API Key is invalid or API access has not been enabled",
# means the credential itself was rejected before the request got as far as
# anything else. It is NOT the non-whitelisted-address message: Namecheap has
# a distinct one for that, 1011150 "Invalid request IP: <address>", which
# _IP_ERROR_RE already matches.
#
# Measured by holding ApiUser fixed and varying only the key, across two
# different commands: the account's real key reached the IP check and came
# back 1011150, while four separate corruptions of that same key - one hex
# digit changed, first character changed, last character changed, and the key
# reversed - were all refused at the credential check with 1011102. The same
# 1011102 comes back when ApiUser names an account that does not exist, so
# the message covers the user as well as the key.
#
# Kept separate from _IP_ERROR_RE because the operator instructions differ:
# an IP error means "add this address to the whitelist", this means "the
# username or the key is not accepted".
_ACCESS_ERROR_RE = re.compile(
    r"api\s*key\s+is\s+invalid"
    r"|api\s+access\s+has\s+not\s+been\s+enabled",
    re.IGNORECASE,
)

# Namecheap parameter -> the .env variable that supplies it. Used to name the
# missing one, rather than sending an empty credential and letting the API's
# complaint about it get swallowed.
_CREDENTIALS = {
    "ApiUser":  "NAMECHEAP_API_USER",
    "ApiKey":   "NAMECHEAP_API_KEY",
    "UserName": "NAMECHEAP_USERNAME",
}


def _load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _update_env_ip(new_ip: str) -> None:
    """Rewrite NAMECHEAP_CLIENT_IP in .env, atomically.

    This used to be ENV_FILE.write_text(...), which opens the real .env with
    mode "w" and so truncates it to zero bytes before writing anything
    (measured: 50 bytes -> 0 at the moment of open). A crash, a full disk or a
    reaped process inside that window leaves the production .env empty, taking
    DATABASE_URL, NAMECHEAP_API_KEY, FLASK_SECRET_KEY and SUPERADMIN_PASSWORD
    with it. Writing a sibling temp file and os.replace()-ing it over the
    target is a rename(2) within one filesystem: a reader sees the old content
    until the new content is complete on disk, and never sees neither.

    os.replace keeps the SOURCE file's permissions, not the destination's
    (measured), and .env must stay 0600 - deploy/install.sh:52,
    DEPLOYMENT.md:312. mkstemp already creates at 0600; when .env exists its
    own mode is carried across so an operator's choice survives.

    new_ip is substituted through a function, not a replacement string: it
    arrives from a third-party HTTP response, and re.sub reads backslash
    escapes in a replacement template.
    """
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    pattern = r"^(NAMECHEAP_CLIENT_IP\s*=\s*).*$"
    replacement = f"NAMECHEAP_CLIENT_IP={new_ip}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, lambda _m: replacement, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{replacement}\n"

    mode = stat.S_IMODE(ENV_FILE.stat().st_mode) if ENV_FILE.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(ENV_FILE.parent),
                               prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, ENV_FILE)
    except BaseException:
        # Leave the original .env exactly as it was, and do not litter the
        # directory with a half-written copy of the secrets.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def get_public_ip() -> str:
    """This host's public IP, as a validated address.

    Every provider's answer is parsed with ipaddress.ip_address before it is
    accepted. Without that check the body of whatever came back - an HTML
    error page, a rate-limit notice, a captive-portal redirect - was written
    verbatim into the production .env, since the only processing was .strip().
    """
    errors = []
    for url in IP_PROVIDERS:
        try:
            candidate = requests.get(url, timeout=10).text.strip()
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {e}")
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            errors.append(f"{url}: answered with {candidate[:60]!r}, "
                          f"which is not an IP address")
            continue
        return candidate
    raise RuntimeError("Could not determine public IP; tried "
                       f"{len(IP_PROVIDERS)} provider(s): " + "; ".join(errors))


def test_namecheap_api(ip: str) -> tuple[bool, str]:
    """
    Returns (whitelisted: bool, message: str).
    Makes a lightweight domains.getList call with PageSize=20.

    message is "OK" only when the API answered Status="OK". When the API
    answers Status="ERROR" for a reason that is not about the IP, the first
    element is still True — the call did reach Namecheap from this address, so
    the whitelist is not the problem — but message carries the API's own text.

    Returning "OK" there is how a dead API key read as a clean run: the only
    record of the real fault was thrown away in this function, and stage 2
    then printed an empty domain list and "0 new domain(s) written".

    Raises RuntimeError if a credential is missing, naming the variable. The
    previous code sent the empty string instead, and the API's complaint about
    it was one of the messages discarded above.
    """
    params = {
        "ApiUser":  os.environ.get("NAMECHEAP_API_USER", ""),
        "ApiKey":   os.environ.get("NAMECHEAP_API_KEY", ""),
        "UserName": os.environ.get("NAMECHEAP_USERNAME", ""),
        "ClientIp": ip,
        "Command":  "namecheap.domains.getList",
        "PageSize": "20",
    }
    missing = [_CREDENTIALS[k] for k in _CREDENTIALS if not params[k]]
    if missing:
        raise RuntimeError("Namecheap credentials are not configured: "
                           + ", ".join(missing) + " must be set in .env")
    try:
        r = requests.get(
            "https://api.namecheap.com/xml.response",
            params=params,
            timeout=15,
        )
        root = ET.fromstring(r.text)
        if root.get("Status") == "ERROR":
            errors = [e.text or "" for e in root.findall(".//nc:Error", NS)]
            msg = "; ".join(errors) or "Status=ERROR with no Error element"
            if _IP_ERROR_RE.search(msg) or _ACCESS_ERROR_RE.search(msg):
                return False, msg
            # Not an IP or access problem: we reached the API from this
            # address and it answered about something else. Hand the message
            # back rather than "OK" so the caller can say so.
            return True, msg
        return True, "OK"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


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
            print("OK" if msg == "OK" else "reachable")
        if msg != "OK":
            # Reached the API from this address, so the whitelist is fine, but
            # Namecheap refused the call for some other reason. Printed
            # whether or not verbose, because the alternative is what this
            # used to do: swallow it, and let stage 2 report an empty domain
            # list as a successful run.
            print(f"  WARNING: Namecheap API error, not IP-related: {msg}")
            print("           The whitelist is not the problem. Later stages "
                  "that call Namecheap will fail.")
        return current_ip

    print("FAILED")
    print()

    # Three different faults land here and they need three different things
    # done about them. Printing the whitelist instructions for all of them —
    # which is what a single block amounts to — sends the operator after the
    # wrong one two times out of three.
    if _ACCESS_ERROR_RE.search(msg):
        print("=" * 60)
        print("  ACTION REQUIRED: Namecheap rejected the credentials")
        print("=" * 60)
        print(f"  Namecheap API settings : {NAMECHEAP_SETTINGS_URL}")
        print()
        print("  This is not the whitelist. A non-whitelisted address gets a")
        print("  different message, 'Invalid request IP'. Namecheap sends")
        print("  this one when the API user or the key is not accepted:")
        print("    1. NAMECHEAP_API_USER and NAMECHEAP_USERNAME must both be")
        print("       the Namecheap account's username — not an email")
        print("       address, and not a display name.")
        print("    2. NAMECHEAP_API_KEY must be the key currently shown on")
        print("       the settings page above.")
        print("    3. API access must be switched ON for the account.")
    elif _IP_ERROR_RE.search(msg):
        # Namecheap's own message names the address it saw ("Invalid request
        # IP: 1.2.3.4"). Prefer that over get_public_ip's answer: that comes
        # from a third party, and on a host whose outbound traffic leaves
        # through a pool of addresses the two differ - observed in one run,
        # ipify said .24 while Namecheap said .21. Whitelisting the address
        # Namecheap did not see fixes nothing.
        seen = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", msg)
        to_add = seen.group(1) if seen else current_ip
        print("=" * 60)
        print("  ACTION REQUIRED: whitelist your IP in Namecheap")
        print("=" * 60)
        print(f"  Your current public IP : {current_ip}")
        if to_add != current_ip:
            print(f"  The address Namecheap saw : {to_add}")
            print("  These differ, so this host's outbound traffic does not")
            print("  always leave from one address. Whitelist what Namecheap")
            print("  saw, and expect to repeat this until the egress is fixed.")
        print(f"  Namecheap API settings : {NAMECHEAP_SETTINGS_URL}")
        print()
        print("  Steps:")
        print("    1. Open the URL above")
        print("    2. Under 'Whitelisted IPs', add:", to_add)
        print("    3. Save, then re-run this script")
    else:
        print("=" * 60)
        print("  Could not reach the Namecheap API")
        print("=" * 60)
        print(f"  Your current public IP : {current_ip}")
        print()
        print("  This is not a whitelist verdict — the call did not complete,")
        print("  so whether this IP is whitelisted is unknown. Check network")
        print("  access to api.namecheap.com, then re-run.")
    print()
    if msg:
        print(f"  API error: {msg}")
    print("=" * 60)
    sys.exit(1)


if __name__ == "__main__":
    print("Checking Namecheap API access...")
    ip = ensure_ip_whitelisted(verbose=True)
    print(f"\nAll good. Namecheap API is accessible from {ip}.")
