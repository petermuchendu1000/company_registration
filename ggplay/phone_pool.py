"""Phone Pool Manager
====================
Manages a pool of phone numbers for Google Play Console registration.
Each company gets one phone that covers:
  - organization_phone  (shown on Play Store)
  - contact_phone       (private; must receive OTP during signup)
  - developer_phone     (public; must receive OTP during signup)

IMPORTANT: OTP verification requires SMS/call capability.
  - VoIP numbers (Zadarma, Skype) often fail Google OTP
  - UK PAYG SIMs (giffgaff, Lebara, Lycamobile) are most reliable
  - Kenyan Safaricom/Airtel numbers work for KE-based operators

Dummy +254 numbers are used until real numbers are supplied.
Replace the pool entries in physical_numbers.json with real numbers.

phone_number format: E.164 international format e.g. +254712345678
"""

from __future__ import annotations

import json
import os
import re

BASE_DIR = os.path.dirname(__file__)

# Primary source: physical_numbers.json  {company_number: "+254..."}
PHONE_JSON = os.path.join(BASE_DIR, "physical_numbers.json")

# Dummy pool used when no real numbers are assigned yet
# Safaricom: 0700-0729, 0110-0119, 0722, 0723, 0725, 0729
# Airtel KE: 0733, 0734, 0735, 0750-0756
# Telkom KE: 0770-0779
_DUMMY_POOL: list[str] = [
    "+254700000001", "+254700000002", "+254700000003", "+254700000004",
    "+254700000005", "+254700000006", "+254700000007", "+254700000008",
    "+254700000009", "+254700000010", "+254711000001", "+254711000002",
    "+254711000003", "+254711000004", "+254711000005", "+254711000006",
    "+254711000007", "+254711000008", "+254711000009", "+254711000010",
    "+254722000001", "+254722000002", "+254722000003", "+254722000004",
    "+254722000005", "+254722000006", "+254722000007", "+254722000008",
    "+254722000009", "+254722000010", "+254733000001", "+254733000002",
    "+254733000003", "+254733000004", "+254733000005", "+254733000006",
    "+254733000007", "+254733000008", "+254733000009", "+254733000010",
    "+254740000001", "+254740000002", "+254740000003", "+254740000004",
    "+254740000005", "+254740000006", "+254740000007", "+254740000008",
    "+254740000009", "+254740000010", "+254750000001", "+254750000002",
    "+254750000003", "+254750000004", "+254750000005", "+254750000006",
    "+254750000007", "+254750000008", "+254750000009", "+254750000010",
    "+254770000001", "+254770000002", "+254770000003", "+254770000004",
    "+254770000005", "+254770000006", "+254770000007", "+254770000008",
    "+254770000009", "+254770000010",
]


def _load_assignments() -> dict[str, str]:
    """Load company_number -> phone from physical_numbers.json (if it exists)."""
    if not os.path.exists(PHONE_JSON):
        return {}
    try:
        with open(PHONE_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}

    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                phone = value.get("phone") or value.get("number") or value.get("phone_number") or ""
            else:
                phone = value or ""
            phone = str(phone).strip()
            if phone:
                out[str(key).strip()] = phone
    return out


def _save_assignments(assignments: dict[str, str]) -> None:
    """Persist company_number -> phone back to physical_numbers.json."""
    try:
        with open(PHONE_JSON, "w", encoding="utf-8") as f:
            json.dump(assignments, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _assigned_phones(assignments: dict[str, str]) -> set[str]:
    return set(assignments.values())


def assign_phone(company_number: str, force_real: bool = False) -> dict:
    """Return the phone record for *company_number*, assigning a dummy if needed.

    Returns a dict:
        {
            "organization_phone": "+254...",
            "contact_phone":       "+254...",
            "developer_phone":     "+254...",
            "phone_type":          "sim" | "voip" | "dummy",
            "otp_capable":         True | False,
            "source":              "physical_numbers.json" | "dummy_pool",
            "is_dummy":            True | False,
        }
    """
    assignments = _load_assignments()
    cn = str(company_number).strip()

    # Already assigned?
    if cn in assignments:
        phone = assignments[cn]
        is_dummy = phone in _DUMMY_POOL
        return _phone_record(phone, is_dummy=is_dummy, source="physical_numbers.json")

    # Pick next unused from dummy pool
    used = _assigned_phones(assignments)
    chosen = None
    for candidate in _DUMMY_POOL:
        if candidate not in used:
            chosen = candidate
            break

    if chosen is None:
        # Pool exhausted — generate a unique extension
        idx = len(assignments) + 1
        chosen = f"+254799{idx:06d}"

    assignments[cn] = chosen
    _save_assignments(assignments)
    return _phone_record(chosen, is_dummy=True, source="dummy_pool")


def _phone_record(phone: str, *, is_dummy: bool, source: str) -> dict:
    return {
        "organization_phone": phone,
        "contact_phone": phone,
        "developer_phone": phone,
        "phone_type": "dummy" if is_dummy else "sim",
        "otp_capable": not is_dummy,
        "source": source,
        "is_dummy": is_dummy,
        "note": (
            "DUMMY NUMBER — replace with real SIM before signup. "
            "giffgaff/Lebara UK PAYG or real Safaricom number required for Google OTP."
        ) if is_dummy else "Real number — verify it can receive SMS/calls for OTP.",
    }


def get_all_assignments() -> dict[str, str]:
    """Return all current company_number -> phone mappings."""
    return _load_assignments()


def set_real_phone(company_number: str, phone: str) -> None:
    """Replace a dummy number with a real phone for *company_number*."""
    phone = phone.strip()
    if not re.match(r"^\+\d{7,15}$", phone):
        raise ValueError(f"Phone must be E.164 format (e.g. +254712345678), got: {phone!r}")
    assignments = _load_assignments()
    assignments[str(company_number).strip()] = phone
    _save_assignments(assignments)
