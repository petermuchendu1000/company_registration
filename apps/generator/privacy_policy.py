"""
Generate a hosted privacy policy URL via the freeprivacypolicy.com REST API.

Flow:
  1. Create a per-company disposable inbox at mail.tm (deterministic address)
  2. Register a new FPP account using that inbox
  3. Read the activation email and call /customer/activate-account
  4. Login → get Bearer token
  5. POST /agreement/new — UK GDPR-compliant privacy policy for an Android app
  6. Return "https://www.freeprivacypolicy.com/live/privacy-policy/{uuid}"

The AES-256-CBC key/IV were reverse-engineered from the frontend JS bundle (module 411).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from typing import Optional

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

logger = logging.getLogger(__name__)

_FPP_API = "https://api.freeprivacypolicy.com/api/v1"
_MAILTM_API = "https://api.mail.tm"
_LIVE_URL_BASE = "https://www.freeprivacypolicy.com/live"

# AES-256-CBC — extracted from frontend JS bundle (module 411, eO function)
_AES_KEY = b"253D3FB468A0E24677C28A624BE0F939"
_AES_IV  = b"12352a1skaj2mAnZ"

_FPP_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://app.freeprivacypolicy.com",
    "Referer": "https://app.freeprivacypolicy.com/",
}


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------

def _encrypt(data: dict) -> str:
    """AES-256-CBC encrypt dict → Base64 string (matches CryptoJS frontend)."""
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    ct = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(ct).decode("ascii")


# ---------------------------------------------------------------------------
# mail.tm helpers  (per-company deterministic inbox)
# ---------------------------------------------------------------------------

def _mailtm_domain() -> str:
    r = requests.get(f"{_MAILTM_API}/domains", timeout=15)
    r.raise_for_status()
    members = r.json().get("hydra:member", [])
    if not members:
        raise RuntimeError("mail.tm: no domains available")
    return members[0]["domain"]


def _mailtm_inbox_password(address: str) -> str:
    return f"Tmp!{hashlib.md5(address.encode()).hexdigest()[:12]}"


def _mailtm_ensure_account(address: str) -> str:
    """Create (or verify existing) mail.tm account. Returns JWT token."""
    password = _mailtm_inbox_password(address)
    # Try to get token first (account may already exist)
    r_tok = requests.post(
        f"{_MAILTM_API}/token",
        json={"address": address, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    if r_tok.status_code == 200:
        return r_tok.json()["token"]
    # Create account
    r_create = requests.post(
        f"{_MAILTM_API}/accounts",
        json={"address": address, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    if r_create.status_code not in (200, 201):
        raise RuntimeError(f"mail.tm account creation failed: {r_create.text[:200]}")
    # Now get token
    r_tok2 = requests.post(
        f"{_MAILTM_API}/token",
        json={"address": address, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    r_tok2.raise_for_status()
    return r_tok2.json()["token"]


def _mailtm_wait_for_activation(inbox_token: str, timeout: int = 90) -> str:
    """Poll inbox for an FPP activation email. Returns activation hash."""
    deadline = time.time() + timeout
    seen: set = set()
    while time.time() < deadline:
        time.sleep(5)
        r = requests.get(
            f"{_MAILTM_API}/messages",
            headers={"Authorization": f"Bearer {inbox_token}"},
            timeout=15,
        )
        if r.status_code != 200:
            continue
        messages = r.json().get("hydra:member", [])
        for meta in messages:
            if meta["id"] in seen:
                continue
            seen.add(meta["id"])
            msg_r = requests.get(
                f"{_MAILTM_API}/messages/{meta['id']}",
                headers={"Authorization": f"Bearer {inbox_token}"},
                timeout=15,
            )
            if msg_r.status_code != 200:
                continue
            msg = msg_r.json()
            subj = msg.get("subject", "")
            logger.debug("[mail.tm] Message subject: %s", subj)
            html_parts = msg.get("html") or []
            text = msg.get("text", "")
            combined = (html_parts[0] if html_parts else "") + text
            h = _extract_activation_hash(combined)
            if h:
                logger.info("[mail.tm] Got activation hash from email")
                return h
    raise RuntimeError("Timed out waiting for FPP activation email")


def _extract_activation_hash(body: str) -> Optional[str]:
    """Extract activation hash from email body (alphanumeric token in URL path)."""
    # FPP activation URL: .../profile/activate-account/{token}
    # Token is alphanumeric [a-z0-9], ~32 chars (NOT hex-only)
    patterns = [
        r'/activate-account/([a-z0-9]{16,})',
        r'hash=([a-zA-Z0-9\-]{16,})',
        r'/activate[^"<\s]*?/([a-z0-9]{16,})',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            # Skip image files
            candidate = m.group(1)
            if '.' not in candidate:
                return candidate
    # Fallback: scan activation URLs for last non-extension path segment
    urls = re.findall(r'https?://[^\s"<>]+activat[^\s"<>]*', body, re.IGNORECASE)
    for url in urls:
        segs = url.rstrip('/').split('/')
        for seg in reversed(segs):
            seg = seg.split('?')[0]
            if len(seg) >= 16 and re.match(r'^[a-z0-9]+$', seg) and '.' not in seg:
                return seg
    return None


# ---------------------------------------------------------------------------
# FPP API helpers
# ---------------------------------------------------------------------------

def _fpp_post(session: requests.Session, path: str, payload: dict,
              token: Optional[str] = None) -> dict:
    headers = dict(_FPP_HEADERS)
    if token:
        headers["X-LCG-Authorization"] = f"Bearer {token}"
    r = session.post(
        f"{_FPP_API}{path}",
        files={"data": (None, _encrypt(payload))},
        headers=headers,
        timeout=30,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError:
        logger.warning("FPP %s: %s %s", path, r.status_code, r.text[:200])
        raise
    return r.json() if r.text else {}


def _fpp_register(session: requests.Session, email: str, password: str) -> bool:
    """Register FPP account. Returns True if new account created."""
    try:
        result = _fpp_post(session, "/customer/register", {
            "email": email, "password": password, "language": "en",
            "send_activation": "1", "initial_password_set": "1",
        })
        return bool(result.get("success"))
    except Exception as e:
        logger.debug("FPP register error: %s", e)
        return False


def _fpp_activate(session: requests.Session, activation_hash: str) -> bool:
    try:
        result = _fpp_post(session, "/customer/activate-account",
                           {"activation_hash": activation_hash})
        return bool(result.get("success"))
    except Exception as e:
        logger.debug("FPP activate error: %s", e)
        return False


def _fpp_login(session: requests.Session, email: str, password: str) -> tuple[str, str]:
    """Login and return (token, customer_id)."""
    result = _fpp_post(session, "/customer/login", {"email": email, "password": password})
    if not result.get("success"):
        raise RuntimeError(f"FPP login failed: {result.get('messages', result)}")
    customer = result.get("data", {}).get("customer") or result.get("data") or {}
    token = customer.get("token")
    customer_id = customer.get("id")
    if not token:
        raise RuntimeError(f"No token in FPP login response: {customer}")
    return token, customer_id


def _fpp_create_agreement(
    session: requests.Session,
    token: str,
    customer_id: str,
    app_name: str,
    company_name: str,
    contact_email: str,
) -> str:
    """Create the privacy policy agreement. Returns agreement UUID."""
    payload = {
        "customer_id": customer_id,
        "agreement_for": ["App"],
        "agreement_type": "PRIVACY_POLICY",
        "agreement_version": "2.0",
        "cost": 0,
        "compliance_california_civil_code_1798": False,
        "compliance_california_business_code_22581": False,
        "app_name": app_name,
        "entity_type": "Business",
        "company_name": company_name,
        "company_address": "United Kingdom",
        "country": {"text": "United Kingdom", "value": "GB"},
        "types_of_data_collected": ["Others"],
        "app_types_of_data_collected": [],
        "service_providers_analytics": False,
        "service_providers_email_marketing": False,
        "service_providers_advertising": False,
        "service_providers_payments": False,
        "service_providers_behavioral_remarketing": False,
        "service_providers_miscellaneous_list": [],
        "compliance_ccpa": False,
        "compliance_gdpr": True,
        "compliance_caloppa": False,
        "compliance_coppa": False,
        "company_contact": ["Email"],
        "company_contact_email": contact_email,
        "translations": ["en"],
    }
    result = _fpp_post(session, "/agreement/new", payload, token=token)
    logger.debug("agreement/new response: %s", str(result)[:300])

    # Response format: {"agreement_id": "uuid", "success": true}
    # or nested: {"data": {"id": "uuid", ...}, "success": true}
    uuid = (
        result.get("agreement_id")
        or result.get("id")
        or result.get("uuid")
    )
    if not uuid:
        data = result.get("data") or {}
        uuid = (
            data.get("agreement_id")
            or data.get("id")
            or data.get("uuid")
        )
    if not uuid:
        for key in ("agreement", "policy"):
            nested = (result.get(key) or {})
            if isinstance(nested, dict):
                uuid = nested.get("id") or nested.get("agreement_id")
                if uuid:
                    break
    if not uuid:
        raise RuntimeError(f"Cannot extract agreement UUID from: {result}")
    return uuid


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_privacy_policy(app_data: dict) -> str:
    """
    Generate a hosted privacy policy and return the live URL.

    app_data keys used:
      display_name   str  App display name → used as app_name in the policy
      support_email  str  Real company contact email (appears in policy text)
      company_name   str  Legal company name (falls back to display_name)
      company_number str  Used to build the deterministic FPP inbox address

    Returns:
      "https://www.freeprivacypolicy.com/live/privacy-policy/{uuid}"
    """
    display_name = app_data.get("display_name", "App")
    support_email = app_data.get("support_email", "")
    company_name = app_data.get("company_name") or display_name
    company_number = app_data.get("company_number") or "0000"

    # Build deterministic disposable inbox for FPP account
    mailtm_domain = _mailtm_domain()
    fpp_inbox = f"fpp{company_number}@{mailtm_domain}"
    fpp_password = f"Pp1!{hashlib.sha256(f'fpp-account-{fpp_inbox}'.encode()).hexdigest()[:16]}"

    logger.info("[FPP] Using inbox %s for FPP account", fpp_inbox)

    # Ensure mail.tm inbox exists
    inbox_token = _mailtm_ensure_account(fpp_inbox)

    session = requests.Session()

    # Try login first (idempotent for subsequent pipeline runs)
    fpp_token: Optional[str] = None
    customer_id: Optional[str] = None
    try:
        fpp_token, customer_id = _fpp_login(session, fpp_inbox, fpp_password)
        logger.info("[FPP] Existing account logged in, customer_id=%s", customer_id)
    except Exception:
        # Register new account
        logger.info("[FPP] Registering new FPP account...")
        _fpp_register(session, fpp_inbox, fpp_password)

        # Wait for activation email
        logger.info("[FPP] Waiting for activation email...")
        act_hash = _mailtm_wait_for_activation(inbox_token)

        # Activate
        logger.info("[FPP] Activating account...")
        _fpp_activate(session, act_hash)
        time.sleep(1)

        # Login
        fpp_token, customer_id = _fpp_login(session, fpp_inbox, fpp_password)
        logger.info("[FPP] Authenticated, customer_id=%s", customer_id)

    # Create the policy
    logger.info("[FPP] Creating privacy policy for '%s'", display_name)
    uuid = _fpp_create_agreement(
        session,
        fpp_token,
        customer_id,
        app_name=display_name,
        company_name=company_name,
        contact_email=support_email or fpp_inbox,
    )

    live_url = f"{_LIVE_URL_BASE}/{uuid}"
    logger.info("[FPP] Live URL: %s", live_url)
    return live_url
