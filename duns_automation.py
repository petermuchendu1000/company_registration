"""
DUNS Number Automation Module
Automates D-U-N-S number lookup and request for UK companies using:
- Companies House API for company data extraction
- mail.tm free temp email API for receiving D&B communications
- D&B web endpoints for DUNS lookup/request
"""

import requests
import json
import time
import random
import string
import re
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY")
CH_BASE = "https://api.company-information.service.gov.uk"

# --- mail.tm Temp Email API ---
MAIL_TM_API = "https://api.mail.tm"

# --- D&B endpoints ---
DNB_DUNS_LOOKUP = "https://www.dnb.com/apps/dnb/servlets/DnBAPI"

# Local persistence file for tracking DUNS requests
DUNS_DB_FILE = os.path.join(os.path.dirname(__file__), "duns_requests.json")


def _load_duns_db():
    if os.path.exists(DUNS_DB_FILE):
        with open(DUNS_DB_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_duns_db(db):
    with open(DUNS_DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


def _extract_director_dob(officer: dict) -> tuple[str, str]:
    """Return normalized DOB value and precision from CH officer payload."""
    dob = officer.get("date_of_birth") or {}
    year = dob.get("year")
    month = dob.get("month")
    day = dob.get("day")

    if year and month and day:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "day"
    if year and month:
        return f"{int(year):04d}-{int(month):02d}", "month"
    if year:
        return f"{int(year):04d}", "year"
    return "", "unknown"


# ============================================================
# COMPANIES HOUSE: Extract company data for DUNS application
# ============================================================

def get_company_data_for_duns(company_number):
    """Extract all company data needed for a D-U-N-S application from Companies House."""
    try:
        # Get company profile
        resp = requests.get(
            f"{CH_BASE}/company/{company_number}",
            auth=(COMPANIES_HOUSE_API_KEY, ""),
            timeout=15,
        )
        resp.raise_for_status()
        profile = resp.json()

        # Get officers
        officers_resp = requests.get(
            f"{CH_BASE}/company/{company_number}/officers",
            auth=(COMPANIES_HOUSE_API_KEY, ""),
            timeout=15,
        )
        officers_data = officers_resp.json() if officers_resp.status_code == 200 else {}

        # Get SIC codes
        sic_codes = profile.get("sic_codes", [])

        # Get registered address
        addr = profile.get("registered_office_address", {})
        address_line_1 = addr.get("address_line_1", "")
        address_line_2 = addr.get("address_line_2", "")
        locality = addr.get("locality", "")
        region = addr.get("region", "")
        postal_code = addr.get("postal_code", "")
        country = addr.get("country", "United Kingdom")

        # Get active directors
        directors = []
        for o in officers_data.get("items", []):
            if not o.get("resigned_on") and "director" in (o.get("officer_role", "") or "").lower():
                dob_value, dob_precision = _extract_director_dob(o)
                directors.append({
                    "name": o.get("name", ""),
                    "role": o.get("officer_role", ""),
                    "nationality": o.get("nationality", ""),
                    "appointed_on": o.get("appointed_on", ""),
                    "date_of_birth": dob_value,
                    "date_of_birth_precision": dob_precision,
                })

        # Determine company age
        date_of_creation = profile.get("date_of_creation", "")
        year_started = ""
        if date_of_creation:
            year_started = date_of_creation[:4]

        # Build the DUNS application data
        company_data = {
            "company_number": company_number,
            "legal_name": profile.get("company_name", ""),
            "company_status": profile.get("company_status", ""),
            "company_type": profile.get("type", ""),
            "date_of_creation": date_of_creation,
            "year_started": year_started,
            "sic_codes": sic_codes,
            "address": {
                "line_1": address_line_1,
                "line_2": address_line_2,
                "locality": locality,
                "region": region,
                "postal_code": postal_code,
                "country": country,
                "full": ", ".join(
                    p for p in [address_line_1, address_line_2, locality, region, postal_code, country] if p
                ),
            },
            "directors": directors,
            "primary_director": directors[0] if directors else None,
            # Fields needed for DUNS application
            "duns_application": {
                "business_name": profile.get("company_name", ""),
                "street_address": address_line_1,
                "street_address_2": address_line_2,
                "city": locality,
                "state_province": region,
                "postal_code": postal_code,
                "country": "GB",
                "country_name": "United Kingdom",
                "phone": "",  # Not available from Companies House - must be provided
                "ceo_name": directors[0]["name"] if directors else "",
                "year_started": year_started,
                "employees": "",  # Not available from Companies House
                "sic_code": sic_codes[0] if sic_codes else "",
                "legal_structure": _map_company_type(profile.get("type", "")),
            },
        }
        return company_data

    except requests.exceptions.HTTPError as e:
        return {"error": f"Companies House API error: {e.response.status_code} - {e.response.text}"}
    except Exception as e:
        return {"error": str(e)}


def _map_company_type(ch_type):
    """Map Companies House company type to D&B legal structure."""
    mapping = {
        "ltd": "Limited Company",
        "private-limited-guarant-nsc": "Limited by Guarantee",
        "private-limited-guarant-nsc-limited-exemption": "Limited by Guarantee",
        "plc": "Public Limited Company (PLC)",
        "private-unlimited": "Unlimited Company",
        "llp": "Limited Liability Partnership (LLP)",
        "scottish-partnership": "Partnership",
        "royal-charter": "Royal Charter",
        "registered-society-non-jurisdictional": "Registered Society",
    }
    return mapping.get(ch_type, "Limited Company")


# ============================================================
# TEMP EMAIL: mail.tm integration
# ============================================================

def _random_string(length=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def create_temp_email():
    """Create a temporary email using mail.tm API. Returns email address, password, and account id."""
    try:
        # Step 1: Get available domains
        resp = requests.get(f"{MAIL_TM_API}/domains", timeout=10)
        resp.raise_for_status()
        domains = resp.json()

        # Handle paginated response
        if isinstance(domains, dict) and "hydra:member" in domains:
            domain_list = domains["hydra:member"]
        elif isinstance(domains, list):
            domain_list = domains
        else:
            return {"error": "Unexpected domains response format"}

        if not domain_list:
            return {"error": "No temp email domains available"}

        domain = domain_list[0]["domain"]

        # Step 2: Create account
        username = _random_string(12)
        address = f"{username}@{domain}"
        password = _random_string(16)

        create_resp = requests.post(
            f"{MAIL_TM_API}/accounts",
            json={"address": address, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        create_resp.raise_for_status()
        account_data = create_resp.json()

        # Step 3: Get auth token
        token_resp = requests.post(
            f"{MAIL_TM_API}/token",
            json={"address": address, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        return {
            "address": address,
            "password": password,
            "account_id": account_data.get("id", ""),
            "token": token_data.get("token", ""),
            "created_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        return {"error": f"Failed to create temp email: {str(e)}"}


def check_temp_email(token, address=None):
    """Check temp email inbox for messages. Returns list of messages."""
    try:
        resp = requests.get(
            f"{MAIL_TM_API}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        messages = []
        items = data.get("hydra:member", data) if isinstance(data, dict) else data

        for msg in items:
            messages.append({
                "id": msg.get("id", ""),
                "from": msg.get("from", {}).get("address", ""),
                "subject": msg.get("subject", ""),
                "intro": msg.get("intro", ""),
                "seen": msg.get("seen", False),
                "created_at": msg.get("createdAt", ""),
                "has_attachments": msg.get("hasAttachments", False),
            })

        return {"messages": messages, "count": len(messages)}

    except Exception as e:
        return {"error": f"Failed to check email: {str(e)}"}


def read_email_message(token, message_id):
    """Read a specific email message. Returns full message content."""
    try:
        resp = requests.get(
            f"{MAIL_TM_API}/messages/{message_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        msg = resp.json()

        return {
            "id": msg.get("id", ""),
            "from": msg.get("from", {}).get("address", ""),
            "to": [t.get("address", "") for t in msg.get("to", [])],
            "subject": msg.get("subject", ""),
            "text": msg.get("text", ""),
            "html": msg.get("html", []),
            "created_at": msg.get("createdAt", ""),
            "has_attachments": msg.get("hasAttachments", False),
            "attachments": msg.get("attachments", []),
        }

    except Exception as e:
        return {"error": f"Failed to read message: {str(e)}"}


def extract_duns_from_email(text):
    """Try to extract a DUNS number (9-digit) from email text."""
    patterns = [
        r'D-?U-?N-?S[^0-9]*(\d{2}-?\d{3}-?\d{4})',
        r'D-?U-?N-?S[^0-9]*(\d{9})',
        r'DUNS\s*(?:Number|No\.?|#)?\s*:?\s*(\d{2}-?\d{3}-?\d{4})',
        r'DUNS\s*(?:Number|No\.?|#)?\s*:?\s*(\d{9})',
        r'(\d{2}-\d{3}-\d{4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            duns = match.group(1).replace("-", "")
            if len(duns) == 9 and duns.isdigit():
                return duns
    return None


# ============================================================
# DUNS LOOKUP: Check if company already has a DUNS
# ============================================================

def lookup_duns_dnb(company_name, country_code="GB", city=""):
    """
    Attempt to look up an existing D-U-N-S number via D&B's public search.
    This uses the public-facing search on dnb.com.
    Returns match info or None.
    """
    try:
        # D&B's business directory search
        search_url = "https://www.dnb.com/apps/dnb/servlets/CompanySearchServlet"
        params = {
            "searchTerm": company_name,
            "countryCode": country_code,
            "city": city,
            "pageNumber": 1,
            "pageSize": 5,
        }
        resp = requests.get(
            search_url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
            timeout=15,
        )

        if resp.status_code == 200:
            try:
                data = resp.json()
                results = data.get("results", data.get("companies", []))
                if results:
                    return {
                        "found": True,
                        "results": results[:5],
                        "source": "dnb_search",
                    }
            except (json.JSONDecodeError, ValueError):
                pass

        return {"found": False, "source": "dnb_search", "note": "No results or endpoint unavailable"}

    except Exception as e:
        return {"found": False, "error": str(e), "source": "dnb_search"}


def stealth_lookup_duns(company_number, company_name="", post_town="",
                        post_code="", email_address="", first_name="",
                        last_name="", headless=True):
    """
    Stealth browser lookup via D&B UK site using Playwright.
    Searches by company registration number first, then by name.
    If email_address is provided, submits the D&B form so they email the DUNS.
    """
    try:
        from browser_stealth import stealth_duns_lookup
        return stealth_duns_lookup(
            company_number=company_number,
            company_name=company_name,
            post_town=post_town,
            post_code=post_code,
            email_address=email_address,
            first_name=first_name,
            last_name=last_name,
            headless=headless,
        )
    except ImportError:
        return {"found": False, "error": "playwright not installed", "source": "stealth_browser"}
    except Exception as e:
        return {"found": False, "error": str(e), "source": "stealth_browser"}


def stealth_request_duns(company_number, headless=True):
    """
    Full automated pipeline: create temp email, get company data from CH,
    run stealth browser to search D&B and submit the DUNS request form
    with the temp email. Then poll for the DUNS email.

    Returns dict with status, temp_email, browser result, and DUNS if found.
    """
    db = _load_duns_db()

    # If already completed, return existing
    if company_number in db and db[company_number].get("duns_number"):
        return {
            "status": "already_completed",
            "company_number": company_number,
            "duns_number": db[company_number]["duns_number"],
        }

    # Step 1: Get company data from Companies House
    company_data = get_company_data_for_duns(company_number)
    if "error" in company_data:
        return {"status": "error", "error": company_data["error"]}

    if company_data.get("company_status") != "active":
        return {"status": "error", "error": f"Company not active: {company_data.get('company_status')}"}

    # Step 2: Create temp email (or reuse existing)
    if company_number in db and db[company_number].get("temp_email", {}).get("address"):
        email_data = db[company_number]["temp_email"]
        # Re-authenticate to get a fresh token
        try:
            token_resp = requests.post(
                f"{MAIL_TM_API}/token",
                json={"address": email_data["address"], "password": email_data["password"]},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if token_resp.status_code == 200:
                email_data["token"] = token_resp.json().get("token", email_data.get("token", ""))
        except Exception:
            pass
    else:
        email_data = create_temp_email()
        if "error" in email_data:
            return {"status": "error", "error": f"Temp email creation failed: {email_data['error']}"}

    # Extract name from primary director for the form
    director = company_data.get("primary_director")
    first_name = "Director"
    last_name = "Director"
    if director and director.get("name"):
        # CH format: "SURNAME, Firstname Middlename"
        parts = director["name"].split(",", 1)
        if len(parts) == 2:
            last_name = parts[0].strip().title()
            first_name = parts[1].strip().split()[0].title() if parts[1].strip() else "Director"
        else:
            name_parts = director["name"].split()
            first_name = name_parts[0].title() if name_parts else "Director"
            last_name = name_parts[-1].title() if len(name_parts) > 1 else "Director"

    addr = company_data.get("address", {})

    # Step 3: Run stealth browser to search + submit form
    browser_result = stealth_lookup_duns(
        company_number=company_number,
        company_name=company_data.get("legal_name", ""),
        post_town=addr.get("locality", ""),
        post_code=addr.get("postal_code", ""),
        email_address=email_data["address"],
        first_name=first_name,
        last_name=last_name,
        headless=headless,
    )

    # Step 4: Store/update tracking record
    record = db.get(company_number, {})
    record.update({
        "company_number": company_number,
        "company_name": company_data.get("legal_name", ""),
        "company_data": company_data,
        "temp_email": {
            "address": email_data["address"],
            "password": email_data.get("password", ""),
            "token": email_data.get("token", ""),
            "account_id": email_data.get("account_id", ""),
        },
        "browser_result": browser_result,
        "status": "submitted" if browser_result.get("duns_emailed") else "search_only",
        "initiated_at": record.get("initiated_at", datetime.utcnow().isoformat()),
        "last_action": datetime.utcnow().isoformat(),
        "duns_number": record.get("duns_number"),
        "emails_received": record.get("emails_received", []),
    })
    db[company_number] = record
    _save_duns_db(db)

    # Step 5: If form was submitted, poll for email (short initial check)
    duns_number = None
    if browser_result.get("duns_emailed"):
        # Wait a bit then check once
        time.sleep(10)
        email_check = check_duns_emails(company_number)
        duns_number = email_check.get("duns_number")

    return {
        "status": "completed" if duns_number else ("submitted" if browser_result.get("duns_emailed") else "search_only"),
        "company_number": company_number,
        "company_name": company_data.get("legal_name", ""),
        "temp_email": email_data["address"],
        "first_name": first_name,
        "last_name": last_name,
        "browser_result": browser_result,
        "duns_number": duns_number,
        "message": (
            f"DUNS number: {duns_number}" if duns_number
            else "Form submitted. D&B will email the DUNS number. Use check-email endpoint to poll."
            if browser_result.get("duns_emailed")
            else browser_result.get("message", "Lookup completed")
        ),
    }


def stealth_apply_duns(company_data, email_address, headless=True):
    """
    Stealth browser D&B DUNS application via 'I'm a Google Developer' flow.
    Pre-fills the form but does NOT auto-submit.
    """
    try:
        from browser_stealth import stealth_duns_apply
        return stealth_duns_apply(
            company_data=company_data,
            email_address=email_address,
            headless=headless,
        )
    except ImportError:
        return {"success": False, "error": "playwright not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def stealth_full_pipeline(company_number, company_name="", post_town="",
                          post_code="", company_data=None,
                          email_address="", headless=True):
    """
    Full stealth pipeline: lookup then apply if not found.
    """
    try:
        from browser_stealth import stealth_duns_full_pipeline
        return stealth_duns_full_pipeline(
            company_number=company_number,
            company_name=company_name,
            post_town=post_town,
            post_code=post_code,
            company_data=company_data,
            email_address=email_address,
            headless=headless,
        )
    except ImportError:
        return {"status": "error", "error": "playwright not installed"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ============================================================
# ORCHESTRATOR: Full DUNS pipeline for a company
# ============================================================

def initiate_duns_request(company_number, phone_number=""):
    """
    Full pipeline:
    1. Get company data from Companies House
    2. Check if already tracked in local DB
    3. Create temp email
    4. Prepare DUNS application data
    5. Store tracking record
    """
    db = _load_duns_db()

    # Check if we already have a request for this company
    if company_number in db:
        existing = db[company_number]
        if existing.get("duns_number"):
            return {
                "status": "already_completed",
                "company_number": company_number,
                "duns_number": existing["duns_number"],
                "message": "DUNS number already obtained",
            }
        return {
            "status": "already_in_progress",
            "company_number": company_number,
            "data": existing,
            "message": "DUNS request already initiated. Check emails for updates.",
        }

    # Step 1: Get company data
    company_data = get_company_data_for_duns(company_number)
    if "error" in company_data:
        return {"status": "error", "error": company_data["error"]}

    if company_data.get("company_status") != "active":
        return {
            "status": "error",
            "error": f"Company is not active (status: {company_data.get('company_status')})",
        }

    # Step 2: Create temp email
    email_data = create_temp_email()
    if "error" in email_data:
        return {"status": "error", "error": email_data["error"]}

    # Step 3: Try DUNS lookup first
    lookup_result = lookup_duns_dnb(
        company_data["legal_name"],
        country_code="GB",
        city=company_data["address"].get("locality", ""),
    )

    # Step 4: Prepare application data
    app_data = company_data["duns_application"]
    app_data["email"] = email_data["address"]
    if phone_number:
        app_data["phone"] = phone_number

    # Step 5: Store tracking record
    record = {
        "company_number": company_number,
        "company_name": company_data["legal_name"],
        "company_data": company_data,
        "temp_email": {
            "address": email_data["address"],
            "password": email_data["password"],
            "token": email_data["token"],
            "account_id": email_data["account_id"],
        },
        "duns_application_data": app_data,
        "lookup_result": lookup_result,
        "duns_number": None,
        "status": "pending",
        "initiated_at": datetime.utcnow().isoformat(),
        "last_checked": None,
        "emails_received": [],
    }

    db[company_number] = record
    _save_duns_db(db)

    return {
        "status": "initiated",
        "company_number": company_number,
        "company_name": company_data["legal_name"],
        "temp_email": email_data["address"],
        "lookup_result": lookup_result,
        "application_data": app_data,
        "message": (
            "DUNS request prepared. Company data extracted from Companies House. "
            "Temp email created for receiving D&B communications. "
            "Use the application data to submit via D&B's website or API."
        ),
        "next_steps": [
            f"1. Use temp email ({email_data['address']}) when applying on dnb.com",
            "2. Submit the application at: https://www.dnb.com/duns/get-a-duns.html",
            "3. Select 'I have an International-based business' or 'I'm a Google Developer'",
            "4. Fill in the pre-populated data from application_data field",
            "5. Monitor emails using the /api/duns/{company_number}/check-email endpoint",
        ],
    }


def check_duns_emails(company_number):
    """Check temp email for DUNS-related messages for a company."""
    db = _load_duns_db()

    if company_number not in db:
        return {"status": "error", "error": "No DUNS request found for this company"}

    record = db[company_number]
    token = record["temp_email"]["token"]

    # Check inbox
    inbox = check_temp_email(token)
    if "error" in inbox:
        # Token might have expired, try re-authenticating
        try:
            token_resp = requests.post(
                f"{MAIL_TM_API}/token",
                json={
                    "address": record["temp_email"]["address"],
                    "password": record["temp_email"]["password"],
                },
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if token_resp.status_code == 200:
                new_token = token_resp.json().get("token", "")
                if new_token:
                    record["temp_email"]["token"] = new_token
                    db[company_number] = record
                    _save_duns_db(db)
                    token = new_token
                    inbox = check_temp_email(token)
        except Exception:
            pass

        if "error" in inbox:
            return {"status": "error", "error": inbox["error"]}

    # Read new messages and look for DUNS numbers
    duns_found = None
    new_messages = []

    for msg in inbox.get("messages", []):
        msg_id = msg["id"]
        # Skip already processed messages
        if msg_id in [e["id"] for e in record.get("emails_received", [])]:
            continue

        # Read full message
        full_msg = read_email_message(token, msg_id)
        if "error" not in full_msg:
            new_messages.append(full_msg)
            record["emails_received"].append({
                "id": msg_id,
                "from": full_msg.get("from", ""),
                "subject": full_msg.get("subject", ""),
                "received_at": full_msg.get("created_at", ""),
            })

            # Try to extract DUNS number
            text_content = full_msg.get("text", "")
            html_content = " ".join(full_msg.get("html", []))
            duns = extract_duns_from_email(text_content) or extract_duns_from_email(html_content)
            if duns:
                duns_found = duns

    if duns_found:
        record["duns_number"] = duns_found
        record["status"] = "completed"
        record["completed_at"] = datetime.utcnow().isoformat()

    record["last_checked"] = datetime.utcnow().isoformat()
    db[company_number] = record
    _save_duns_db(db)

    return {
        "status": "completed" if duns_found else "pending",
        "company_number": company_number,
        "company_name": record["company_name"],
        "duns_number": duns_found,
        "temp_email": record["temp_email"]["address"],
        "total_emails": len(record["emails_received"]),
        "new_emails": len(new_messages),
        "new_messages": [
            {
                "from": m.get("from", ""),
                "subject": m.get("subject", ""),
                "preview": (m.get("text", "") or "")[:200],
            }
            for m in new_messages
        ],
        "last_checked": record["last_checked"],
    }


def get_all_duns_requests():
    """Get summary of all tracked DUNS requests."""
    db = _load_duns_db()
    summary = []
    for cn, record in db.items():
        summary.append({
            "company_number": cn,
            "company_name": record.get("company_name", ""),
            "status": record.get("status", "unknown"),
            "duns_number": record.get("duns_number"),
            "temp_email": record.get("temp_email", {}).get("address", ""),
            "initiated_at": record.get("initiated_at", ""),
            "last_checked": record.get("last_checked"),
            "emails_count": len(record.get("emails_received", [])),
        })
    return summary


def get_duns_request_detail(company_number):
    """Get full detail of a specific DUNS request."""
    db = _load_duns_db()
    if company_number not in db:
        return None
    record = db[company_number]
    return {
        "company_number": company_number,
        "company_name": record.get("company_name", ""),
        "status": record.get("status", "unknown"),
        "duns_number": record.get("duns_number"),
        "temp_email": record.get("temp_email", {}).get("address", ""),
        "application_data": record.get("duns_application_data", {}),
        "lookup_result": record.get("lookup_result"),
        "company_data": record.get("company_data", {}),
        "initiated_at": record.get("initiated_at", ""),
        "last_checked": record.get("last_checked"),
        "emails_received": record.get("emails_received", []),
    }


def manually_set_duns(company_number, duns_number):
    """Manually set a DUNS number for a company (if obtained outside automation)."""
    duns_number = duns_number.replace("-", "").strip()
    if len(duns_number) != 9 or not duns_number.isdigit():
        return {"error": "Invalid DUNS number. Must be 9 digits."}

    db = _load_duns_db()
    if company_number not in db:
        return {"error": "No DUNS request found for this company. Initiate first."}

    db[company_number]["duns_number"] = duns_number
    db[company_number]["status"] = "completed"
    db[company_number]["completed_at"] = datetime.utcnow().isoformat()
    _save_duns_db(db)

    return {
        "status": "completed",
        "company_number": company_number,
        "duns_number": duns_number,
    }
