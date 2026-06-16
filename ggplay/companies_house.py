import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY")
BASE_URL = "https://api.company-information.service.gov.uk"


def search_companies(query, items_per_page=10):
    """Search for companies by name."""
    resp = requests.get(
        f"{BASE_URL}/search/companies",
        params={"q": query, "items_per_page": items_per_page},
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_company(company_number):
    """Get full company profile by company number."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}",
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_officers(company_number):
    """Get company officers (directors, secretaries, etc.)."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}/officers",
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_filing_history(company_number, items_per_page=10):
    """Get company filing history."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}/filing-history",
        params={"items_per_page": items_per_page},
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_persons_with_significant_control(company_number):
    """Get persons with significant control (PSC)."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}/persons-with-significant-control",
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_charges(company_number):
    """Get company charges (mortgages, etc.)."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}/charges",
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def get_insolvency(company_number):
    """Get company insolvency information."""
    resp = requests.get(
        f"{BASE_URL}/company/{company_number}/insolvency",
        auth=(API_KEY, ""),
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_document_metadata(document_id):
    """Get metadata for a filing document."""
    resp = requests.get(
        f"{BASE_URL}/document/{document_id}",
        auth=(API_KEY, ""),
    )
    resp.raise_for_status()
    return resp.json()


def download_document(document_id, output_path):
    """Download a filing document (PDF) to disk."""
    resp = requests.get(
        f"{BASE_URL}/document/{document_id}/content",
        auth=(API_KEY, ""),
        headers={"Accept": "application/pdf"},
        stream=True,
    )
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return output_path


def download_all_filings(company_number, output_dir, items_per_page=50):
    """Download all available filing documents for a company."""
    os.makedirs(output_dir, exist_ok=True)
    filings = get_filing_history(company_number, items_per_page=items_per_page)
    downloaded = []
    for item in filings.get("items", []):
        doc_link = item.get("links", {}).get("document_metadata")
        if not doc_link:
            continue
        doc_id = doc_link.rstrip("/").split("/")[-1]
        date = item.get("date", "unknown")
        desc = item.get("description", "filing").replace(" ", "_")[:40]
        filename = f"{date}_{desc}_{doc_id[:8]}.pdf"
        filepath = os.path.join(output_dir, filename)
        try:
            download_document(doc_id, filepath)
            downloaded.append(filepath)
            print(f"  Downloaded: {filename}")
        except Exception as e:
            print(f"  Failed: {filename} - {e}")
    return downloaded


def get_all_company_info(company_number):
    """Retrieve all available info for a company."""
    info = {"profile": get_company(company_number)}

    try:
        info["officers"] = get_officers(company_number)
    except Exception:
        info["officers"] = None

    try:
        info["psc"] = get_persons_with_significant_control(company_number)
    except Exception:
        info["psc"] = None

    try:
        info["filing_history"] = get_filing_history(company_number)
    except Exception:
        info["filing_history"] = None

    try:
        info["charges"] = get_charges(company_number)
    except Exception:
        info["charges"] = None

    info["insolvency"] = get_insolvency(company_number)

    return info


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python companies_house.py <company_name>        - Search by name")
        print("  python companies_house.py <company_number>      - Get all info")
        print("  python companies_house.py --docs <company_number> - Download all filing documents")
        sys.exit(1)

    # Download documents mode
    if sys.argv[1] == "--docs":
        if len(sys.argv) < 3:
            print("Provide a company number: python companies_house.py --docs 00445790")
            sys.exit(1)
        cn = sys.argv[2].zfill(8)
        out_dir = os.path.join("documents", cn)
        print(f"\nDownloading filings for {cn} into {out_dir}/...\n")
        files = download_all_filings(cn, out_dir)
        print(f"\nDone. {len(files)} documents downloaded.")
        sys.exit(0)

    query = " ".join(sys.argv[1:])

    # If it looks like a company number (digits only, or prefix like SC/OC/NI + digits)
    stripped = query.replace(" ", "").upper()
    is_number = stripped.isdigit() or (len(stripped) <= 8 and stripped[:2] in ("SC", "OC", "NI", "RC", "IP") and stripped[2:].isdigit())
    if is_number:
        padded = query.zfill(8)
        print(f"\nFetching company {padded}...\n")
        data = get_all_company_info(padded)
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"\nSearching for '{query}'...\n")
        results = search_companies(query)
        for item in results.get("items", []):
            print(f"  {item['company_number']}  {item['title']}  ({item.get('company_status', 'N/A')})")
        print(f"\nTotal results: {results.get('total_results', 0)}")
