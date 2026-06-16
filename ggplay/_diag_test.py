"""Full pipeline diagnostic - run with: python _diag_test.py"""
import sys, os

PASS = "[OK  ]"
FAIL = "[FAIL]"

print("=" * 60)
print("PIPELINE DIAGNOSTIC")
print("=" * 60)

# ── 1. Email name extraction ─────────────────────────────────
print("\n=== 1. EMAIL NAME EXTRACTION ===")
from email_pool import extract_name_from_email
tests = [
    ("faithkabebee@gmail.com",     ("Faith", "Kabebee")),
    ("john.odhiambo@gmail.com",    ("John",  "Odhiambo")),
    ("wanjiku.mwangi123@gmail.com",("Wanjiku","Mwangi")),
    ("cmuriithi@gmail.com",        ("C",     None)),  # short - just want not empty
    ("achieng_moraa@gmail.com",    ("Achieng","Moraa")),
]
for email, (exp_first, exp_last) in tests:
    f, l = extract_name_from_email(email)
    ok = (f == exp_first) and (exp_last is None or l == exp_last)
    print(f"  {PASS if ok else FAIL}  {email} -> '{f} {l}'")

# ── 2. Address normalization ─────────────────────────────────
print("\n=== 2. ADDRESS NORMALIZATION ===")
from play_console_readiness import _normalize_address, _compare_addresses
addr_pairs = [
    ("123 High St, London, EC1A 1BB",          "123 High Street, London, EC1A 1BB",       True),
    ("14 St Margarets Road, London",           "14 Saint Margarets Road, London",          True),
    ("Unit 5, Victoria Rd, Birmingham",        "Unit 5, Victoria Road, Birmingham",        True),
    ("10 Church Ave, Leeds, LS1 1AA",          "10 Church Avenue, Leeds LS1 1AA",          True),
    ("45 Oxford St, London W1D 1BS",           "45 Oxford Street, London, W1D 1BS, England", True),
    ("123 Fake Street, London",                "456 Different Road, London",               False),  # should NOT match
]
all_addr_ok = True
for a, b, expected_match in addr_pairs:
    r = _compare_addresses(a, b)
    ok = r["match"] == expected_match
    all_addr_ok = all_addr_ok and ok
    na = _normalize_address(a)
    nb = _normalize_address(b)
    label = PASS if ok else FAIL
    print(f"  {label}  '{a[:35]}'")
    print(f"         vs '{b[:35]}'")
    print(f"         norm: '{na}' vs '{nb}'  match={r['match']} expected={expected_match}  detail={r['detail']}")

# ── 3. Developer display name ────────────────────────────────
print("\n=== 3. DEVELOPER DISPLAY NAME (should be from Gmail) ===")
from play_console_readiness import build_readiness_record
result_with_email = {
    "company_number": "14123456",
    "details": {
        "company_name": "FLORENCE HEALTHCARE LIMITED",
        "company_status": "active",
        "address": "45 Oxford St, London, W1D 1BS",
        "directors": [{"name": "ODHIAMBO, Faith Wanjiku", "nationality": "Kenyan", "appointed_on": "2022-01-15"}],
    },
    "duns": {"duns_number": "218456789", "status": "found", "dnb_name": "FLORENCE HEALTHCARE LIMITED",
             "dnb_address": "45 Oxford Street, London, W1D 1BS, England"},
    "domain": {"domain": "florence-healthcare.online", "status": "registered"},
    "email": {"email": "faithkabebee@gmail.com", "account_name": "Faith Kabebee"},
    "play_console": {
        "developer_email": "dev@florence-healthcare.online",
        "developer_email_forwarding": {"status": "configured"},
        "google_txt": {"status": "token_pending", "value": "", "hostname": "@"},
    },
}
rec = build_readiness_record(result_with_email)
ddn = rec["organization"]["developer_display_name"]
# It SHOULD be "Faith Kabebee" (from Gmail), NOT "Florence Healthcare"
expected = "Faith Kabebee"
ok = ddn == expected
print(f"  {PASS if ok else FAIL}  developer_display_name = '{ddn}' (expected: '{expected}')")

# ── 4. Full readiness record ─────────────────────────────────
print("\n=== 4. FULL READINESS RECORD ===")
print(f"  Name match: {rec['duns']['name_match']} — {rec['duns']['name_match_detail']}")
print(f"  Addr match: {rec['duns']['address_match']} — {rec['duns']['address_match_detail']}")
print(f"  Org phone:  {rec['organization']['organization_phone']}")
print(f"  Phone dummy: {rec['developer_contact']['phone_is_dummy']}")
print(f"  Org website: {rec['organization']['organization_website']}")
print(f"  Contact:    {rec['contact']['contact_name']}")
print(f"  Score:      {rec['readiness']['score']}")
print(f"  Missing:    {rec['readiness']['blocking_missing']}")

# ── 5. Listing.py phone gap ───────────────────────────────────
print("\n=== 5. LISTING.PY PHONE ===")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apps.generator.listing import listing_payload
payload = listing_payload({
    "display_name": "Florence Healthcare",
    "role_noun": "shift",
    "export_title": "Shift Log",
    "domain": "florence-healthcare.online",
    "application_id": "uk.c14123456.shift",
    "support_email": "dev@florence-healthcare.online",
    "organization_phone": "+254700000001",
})
dev_phone = payload["store_listing"]["developer_phone"]
ok = dev_phone != "PENDING_ORGANIZATION_PHONE"
print(f"  {PASS if ok else FAIL}  developer_phone = '{dev_phone}'")

# ── 6. Catalog Company fields ─────────────────────────────────
print("\n=== 6. CATALOG COMPANY ===")
from apps.generator.catalog import Company
c = Company(company_number="14123456", company_name="FLORENCE HEALTHCARE LIMITED",
            sic_codes="86210", domain="florence-health.online",
            support_email="dev@florence-health.online", archetype="shift")
print(f"  display_name (app label): '{c.display_name}'")
print(f"  Has developer_display_name field: {hasattr(c, 'developer_display_name')}")
print(f"  Has organization_phone field: {hasattr(c, 'organization_phone')}")

# ── 7. Phone pool ──────────────────────────────────────────────
print("\n=== 7. PHONE POOL ===")
from phone_pool import assign_phone
p = assign_phone("TEST_DIAG_001")
print(f"  Assigned: {p['organization_phone']} type={p['phone_type']} dummy={p['is_dummy']} otp={p['otp_capable']}")
p2 = assign_phone("TEST_DIAG_001")  # same company = same number
ok = p["organization_phone"] == p2["organization_phone"]
print(f"  {PASS if ok else FAIL}  Idempotent assignment: {p['organization_phone']} == {p2['organization_phone']}")

print("\n" + "=" * 60)
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
