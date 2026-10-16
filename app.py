from flask import Flask, request, jsonify, Response
import requests as http_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
import json
import time
from collections import OrderedDict
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static")

# --- Wizard onboarding UI ---
from wizard_routes import register_wizard_routes
register_wizard_routes(app)

# --- Company Pipeline API v2 ---
from api_v2 import api_v2
app.register_blueprint(api_v2)

# --- Authentication (must come after every blueprint is registered, so the
# deny-by-default guard covers their routes too) ---
from auth import init_auth
init_auth(app)


API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY")
BASE_URL = "https://api.company-information.service.gov.uk"

# --- Connection pooling with retry ---
_session = http_requests.Session()
_adapter = HTTPAdapter(
    pool_connections=10,
    pool_maxsize=20,
    max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503]),
)
_session.mount("https://", _adapter)

# --- LRU Cache ---
_cache = OrderedDict()
_CACHE_MAX = 500
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            _cache.move_to_end(key)
            return val
        del _cache[key]
    return None


def _cache_set(key, val):
    _cache[key] = (val, time.time())
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def ch_get(path, params=None):
    cache_key = f"{path}|{json.dumps(params, sort_keys=True) if params else ''}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    for attempt in range(5):
        resp = _session.get(
            f"{BASE_URL}{path}",
            params=params,
            auth=(API_KEY, ""),
            timeout=15,
        )
        if resp.status_code == 429:
            wait = min(2 ** attempt * 2, 30)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        _cache_set(cache_key, data)
        return data
    # Final attempt failed
    raise Exception(f"Rate limited after 5 retries: {path}")


def ch_get_raw(url, headers=None, stream=False):
    """Raw GET for document downloads (no caching), with 429 backoff."""
    for attempt in range(5):
        resp = _session.get(url, auth=(API_KEY, ""), headers=headers, stream=stream, timeout=30)
        if resp.status_code == 429:
            wait = min(2 ** attempt * 2, 30)
            time.sleep(wait)
            continue
        return resp
    return resp  # return last response even if 429


@app.route("/")
def index():
    # Send each role to the surface it is allowed to use. A client loading
    # the operator console would get a page whose every control 403s.
    from flask import redirect, g
    user = getattr(g, "user", None)
    if user and str(user.get("role") or "client") == "client":
        return redirect("/portal")
    return redirect("/app")


# --- Company detail endpoints ---

@app.route("/api/company/<company_number>")
def api_company(company_number):
    try:
        return jsonify(ch_get(f"/company/{company_number}"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company/<company_number>/officers")
def api_officers(company_number):
    try:
        return jsonify(ch_get(f"/company/{company_number}/officers"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company/<company_number>/pscs")
def api_pscs(company_number):
    try:
        return jsonify(ch_get(f"/company/{company_number}/persons-with-significant-control"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company/<company_number>/filings")
def api_filings(company_number):
    try:
        return jsonify(ch_get(f"/company/{company_number}/filing-history", {"items_per_page": 50}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/company/<company_number>/incorporation-certificate")
def api_incorporation_certificate(company_number):
    try:
        data = ch_get(f"/company/{company_number}/filing-history", {"items_per_page": 100})

        # Strategy 1: Look for incorporation filing
        doc_link = None
        for item in data.get("items", []):
            cat = (item.get("category") or "").lower()
            desc = (item.get("description") or "").lower()
            ftype = (item.get("type") or "").upper()
            if cat == "incorporation" or ftype == "NEWINC" or "incorporat" in desc:
                doc_link = item.get("links", {}).get("document_metadata")
                if doc_link:
                    break

        # Strategy 2: For foreign companies (FC/SF) or older companies, get the earliest filing with a document
        if not doc_link:
            filings_with_docs = [
                item for item in data.get("items", [])
                if item.get("links", {}).get("document_metadata")
            ]
            if filings_with_docs:
                # Earliest filing (last in list)
                doc_link = filings_with_docs[-1].get("links", {}).get("document_metadata")

        if not doc_link:
            return jsonify({"error": "No documents available for this company"}), 404

        # Stream the PDF
        content_url = f"{doc_link}/content"
        resp = ch_get_raw(content_url, headers={"Accept": "application/pdf"}, stream=True)
        resp.raise_for_status()
        return Response(
            resp.iter_content(chunk_size=8192),
            content_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=Certificate_of_Incorporation_{company_number}.pdf"
            },
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- DUNS Automation endpoints ---

from duns_automation import (
    initiate_duns_request,
    check_duns_emails,
    get_all_duns_requests,
    get_duns_request_detail,
    get_company_data_for_duns,
    manually_set_duns,
    stealth_lookup_duns,
    stealth_request_duns,
    stealth_apply_duns,
    stealth_full_pipeline,
)


@app.route("/api/duns/initiate/<company_number>", methods=["POST"])
def api_duns_initiate(company_number):
    """Initiate DUNS number retrieval for a company."""
    try:
        phone = ""
        if request.is_json:
            phone = request.json.get("phone", "")
        result = initiate_duns_request(company_number, phone_number=phone)
        status_code = 200 if result.get("status") != "error" else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/check-email")
def api_duns_check_email(company_number):
    """Check temp email for DUNS communications."""
    try:
        result = check_duns_emails(company_number)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/detail")
def api_duns_detail(company_number):
    """Get full DUNS request details for a company."""
    try:
        result = get_duns_request_detail(company_number)
        if result is None:
            return jsonify({"error": "No DUNS request found"}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/set-duns", methods=["POST"])
def api_duns_set(company_number):
    """Manually set DUNS number for a company."""
    try:
        data = request.get_json()
        if not data or not data.get("duns_number"):
            return jsonify({"error": "duns_number required"}), 400
        result = manually_set_duns(company_number, data["duns_number"])
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/all")
def api_duns_all():
    """Get summary of all DUNS requests."""
    try:
        return jsonify(get_all_duns_requests())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/company-data")
def api_duns_company_data(company_number):
    """Get pre-extracted company data formatted for DUNS application."""
    try:
        result = get_company_data_for_duns(company_number)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/stealth-lookup", methods=["POST"])
def api_duns_stealth_lookup(company_number):
    """Stealth browser lookup for DUNS via D&B UK site."""
    try:
        data = request.get_json(silent=True) or {}
        headless = data.get("headless", True)

        # Get company data from CH to have name/address for fallback search
        company_data = get_company_data_for_duns(company_number)
        company_name = company_data.get("legal_name", "") if "error" not in company_data else ""
        addr = company_data.get("address", {}) if "error" not in company_data else {}

        # Email/name can come from request body or existing tracking record
        email = data.get("email", "")
        first_name = data.get("first_name", "")
        last_name = data.get("last_name", "")
        if not email:
            from duns_automation import _load_duns_db
            db = _load_duns_db()
            if company_number in db:
                email = db[company_number].get("temp_email", {}).get("address", "")

        result = stealth_lookup_duns(
            company_number=company_number,
            company_name=company_name,
            post_town=addr.get("locality", ""),
            post_code=addr.get("postal_code", ""),
            email_address=email,
            first_name=first_name,
            last_name=last_name,
            headless=headless,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/stealth-request", methods=["POST"])
def api_duns_stealth_request(company_number):
    """Full automated DUNS request: creates temp email, searches D&B, submits form, polls for DUNS email."""
    try:
        data = request.get_json(silent=True) or {}
        headless = data.get("headless", True)
        result = stealth_request_duns(company_number, headless=headless)
        status_code = 200 if result.get("status") != "error" else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/stealth-apply", methods=["POST"])
def api_duns_stealth_apply(company_number):
    """Stealth browser DUNS application via the D&B developer flow."""
    try:
        data = request.get_json(silent=True) or {}
        headless = data.get("headless", True)

        # Get company data and tracking record
        company_data = get_company_data_for_duns(company_number)
        if "error" in company_data:
            return jsonify(company_data), 400

        # Get email from tracking DB or request body
        email = data.get("email", "")
        if not email:
            from duns_automation import _load_duns_db
            db = _load_duns_db()
            if company_number in db:
                email = db[company_number].get("temp_email", {}).get("address", "")

        if not email:
            return jsonify({"error": "No email available. Initiate DUNS request first or provide 'email' in body."}), 400

        result = stealth_apply_duns(
            company_data=company_data,
            email_address=email,
            headless=headless,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duns/<company_number>/stealth-full", methods=["POST"])
def api_duns_stealth_full(company_number):
    """Full stealth pipeline: lookup then prepare application if not found."""
    try:
        data = request.get_json(silent=True) or {}
        headless = data.get("headless", True)

        # Get company data
        company_data = get_company_data_for_duns(company_number)
        if "error" in company_data:
            return jsonify(company_data), 400

        addr = company_data.get("address", {})

        # Get email from tracking DB
        email = data.get("email", "")
        if not email:
            from duns_automation import _load_duns_db
            db = _load_duns_db()
            if company_number in db:
                email = db[company_number].get("temp_email", {}).get("address", "")

        result = stealth_full_pipeline(
            company_number=company_number,
            company_name=company_data.get("legal_name", ""),
            post_town=addr.get("locality", ""),
            post_code=addr.get("postal_code", ""),
            company_data=company_data,
            email_address=email,
            headless=headless,
        )

        # Auto-store if found
        if result.get("duns_number"):
            from duns_automation import _load_duns_db, _save_duns_db
            db = _load_duns_db()
            if company_number in db:
                db[company_number]["duns_number"] = result["duns_number"]
                db[company_number]["status"] = "completed"
                db[company_number]["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                db[company_number]["completed_via"] = "stealth_full_pipeline"
                _save_duns_db(db)

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import os

    # Fail here, by name, rather than at the first database call or the first
    # stage that needs a credential. `python config.py check` says what each
    # setting is and which stages it unlocks.
    import config
    config.require_startup_settings()
    _status = config.inspect()
    for _warning in _status.warnings:
        print(f"  [config] warning: {_warning}")
    if _status.stages_blocked:
        print(f"  [config] {len(_status.stages_ready)}/{len(config.STAGES)} stages ready; "
              f"unavailable: {', '.join(sorted(_status.stages_blocked))}")

    start_port = int(os.getenv("PORT", 5000))
    max_tries = 10

    # Company files used to live in one flat tree keyed on company number and
    # name, with no record of the owning tenant. Move any such directory into
    # its tenant's subtree before serving; see company_paths.
    try:
        import company_paths
        for _note in company_paths.migrate_all():
            print(f"  [company-paths] {_note}")
    except Exception as _e:  # noqa: BLE001 - never block startup on migration
        print(f"  [company-paths] migration skipped: {_e}")

    # Debug mode exposes the Werkzeug console, which is remote code execution
    # for anyone who can reach the port. Opt in explicitly, for local work only.
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    host = os.getenv("HOST", "127.0.0.1")
    if debug and host != "127.0.0.1":
        raise SystemExit("Refusing to run debug mode on a non-loopback host.")

    for p in range(start_port, start_port + max_tries):
        try:
            print(f"Attempting to start server on port {p}...")
            app.run(debug=debug, host=host, port=p, threaded=True)
            break
        except OSError as e:
            print(f"Failed to bind to port {p}: {e}")
            # try next port
    else:
        raise SystemExit(f"Could not bind to any port in range {start_port}-{start_port+max_tries-1}")
