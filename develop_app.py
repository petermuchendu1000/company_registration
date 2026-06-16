"""
Develop a single app for a company.

Usage:
    python develop_app.py "Company Name Ltd"

This script:
1. Searches Companies House for the company
2. Gets company details, DUNS, certificate, domain, email
3. Generates a branded Android app
4. Saves results to Excel and outputs the APK
"""

import argparse
import os
import sys
from pathlib import Path

# Import pipeline functions
from run_pipeline import (
    ch_get, get_company_details, get_duns_number, download_certificate,
    register_company_domain, _safe_folder_name
)
from companies_house import search_companies
from apps.generator.generate import generate_from_pipeline_results


def find_company_by_name(company_name):
    """Search Companies House for a company by name and return the first active result."""
    try:
        data = search_companies(company_name, items_per_page=5)
        items = data.get("items", [])

        # Filter for active companies
        active_companies = [
            item for item in items
            if item.get("company_status", "").lower() == "active"
        ]

        if not active_companies:
            print(f"No active companies found for '{company_name}'")
            return None

        # Return the first active company
        company = active_companies[0]
        return {
            "company_number": company.get("company_number", ""),
            "company_name": company.get("title", ""),
        }

    except Exception as e:
        print(f"Error searching for company: {e}")
        return None


def develop_app(company_name, skip_duns=False, skip_cert=False, skip_domain=False, skip_email=False, headless=True, artifact="apk"):
    """Develop an app for a single company."""
    print("=" * 60)
    print(f"DEVELOP APP - {company_name}")
    print("=" * 60)

    # Step 1: Find the company
    print(f"[1] Searching for company '{company_name}'...")
    company_info = find_company_by_name(company_name)
    if not company_info:
        print("Company not found. Exiting.")
        return None

    company_number = company_info["company_number"]
    found_name = company_info["company_name"]
    print(f"    Found: {company_number} - {found_name}")

    # Step 2: Get company details
    print(f"[2] Getting company details...")
    try:
        details = get_company_details(company_number)
        print(f"    Name: {details['company_name']}")
        print(f"    Address: {details['address']}")
        print(f"    Directors: {details['director_names']}")
        print(f"    SIC: {details['sic_codes']}")
    except Exception as e:
        print(f"    ERROR: {e}")
        return None

    result = {"company_number": company_number, "details": details}

    # Step 3: Get DUNS
    if skip_duns:
        print(f"[3] DUNS: skipped")
        result["duns"] = {"status": "skipped"}
    else:
        print(f"[3] Looking up DUNS number...")
        try:
            duns_result = get_duns_number(company_number, headless=headless)
            result["duns"] = {
                "duns_number": duns_result.get("duns_number"),
                "status": duns_result.get("status", "unknown"),
            }
            if duns_result.get("duns_number"):
                print(f"    DUNS: {duns_result['duns_number']}")
            else:
                print(f"    Status: {duns_result.get('status', 'unknown')}")
        except Exception as e:
            print(f"    ERROR: {e}")
            result["duns"] = {"status": "error", "error": str(e)}

    # Step 4: Download certificate
    certs_base_dir = os.path.join(os.path.dirname(__file__), "certificates")
    company_folder = os.path.join(certs_base_dir, _safe_folder_name(details["company_name"]))

    if skip_cert:
        print(f"[4] Certificate: skipped")
        result["certificate"] = {"status": "skipped"}
    else:
        print(f"[4] Downloading certificate...")
        try:
            cert_path, cert_error = download_certificate(company_number, company_folder)
            if cert_path:
                result["certificate"] = {"path": cert_path, "status": "downloaded"}
                print(f"    Saved: {cert_path}")
            else:
                result["certificate"] = {"error": cert_error, "status": "failed"}
                print(f"    Failed: {cert_error}")
        except Exception as e:
            print(f"    ERROR: {e}")
            result["certificate"] = {"error": str(e), "status": "error"}

    # Step 5: Register domain
    if skip_domain:
        print(f"[5] Domain: skipped")
        result["domain"] = {"status": "skipped"}
    else:
        print(f"[5] Registering domain...")
        try:
            domain_result = register_company_domain(details["company_name"], details)
            result["domain"] = domain_result
            if domain_result.get("status") == "registered":
                print(f"    Domain: {domain_result['domain']} (${domain_result.get('charged', '?')})")
            else:
                print(f"    Failed: {domain_result.get('error', 'unknown')}")
        except Exception as e:
            print(f"    ERROR: {e}")
            result["domain"] = {"status": "error", "error": str(e)}

    # Step 6: Assign email
    if skip_email:
        print(f"[6] Email: skipped")
        result["email"] = {"status": "skipped"}
    else:
        print(f"[6] Assigning email...")
        try:
            from email_pool import EmailPool
            email_pool = EmailPool()
            email, first_name, last_name = email_pool.assign_next(
                company_number=company_number,
                company_name=details["company_name"],
            )
            account_name = f"{first_name} {last_name}"
            result["email"] = {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "account_name": account_name,
                "status": "assigned",
            }
            print(f"    Email: {email}")
            print(f"    Account name: {account_name}")
        except Exception as e:
            print(f"    ERROR: {e}")
            result["email"] = {"status": "error", "error": str(e)}

    # Step 7: Generate the app
    print(f"[7] Generating Android app...")
    try:
        apk_results = generate_from_pipeline_results([result], variant="Release", artifact=artifact)
        apk_info = apk_results.get(company_number, {})
        if apk_info.get("status") == "success":
            if apk_info.get("aab_path"):
                print(f"    AAB: {apk_info['aab_path']}")
            if apk_info.get("apk_path"):
                print(f"    APK: {apk_info['apk_path']}")
            result["apk"] = apk_info
        else:
            print(f"    Failed: {apk_info.get('error', 'unknown')}")
            result["apk"] = apk_info
    except Exception as e:
        print(f"    ERROR: {e}")
        result["apk"] = {"status": "error", "error": str(e)}

    # Step 8: Save to Excel
    print(f"[8] Saving to Excel...")
    try:
        from run_pipeline import save_to_excel
        save_to_excel([result], os.path.join(os.path.dirname(__file__), "pipeline_output", "companies_pipeline.xlsx"))
        print("    Results saved to Excel")
    except Exception as e:
        print(f"    ERROR saving to Excel: {e}")

    print(f"\n{'='*60}")
    print("APP DEVELOPMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Company: {details['company_name']} ({company_number})")
    if result.get("duns", {}).get("duns_number"):
        print(f"DUNS: {result['duns']['duns_number']}")
    if result.get("domain", {}).get("domain"):
        print(f"Domain: {result['domain']['domain']}")
    if result.get("email", {}).get("email"):
        print(f"Email: {result['email']['email']}")
    if result.get("apk", {}).get("apk_path"):
        print(f"APK: {result['apk']['apk_path']}")
    if result.get("apk", {}).get("aab_path"):
        print(f"AAB: {result['apk']['aab_path']}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Develop an app for a single company")
    parser.add_argument("company_name", help="Name of the company to develop an app for")
    parser.add_argument("--no-duns", action="store_true", help="Skip DUNS lookup")
    parser.add_argument("--no-cert", action="store_true", help="Skip certificate download")
    parser.add_argument("--no-domain", action="store_true", help="Skip domain registration")
    parser.add_argument("--no-email", action="store_true", help="Skip email assignment")
    parser.add_argument("--artifact", default="apk", choices=["apk", "aab", "both"], help="Artifact type to build")
    parser.add_argument("--headless", default="true", help="Browser headless mode for DUNS (true/false)")

    args = parser.parse_args()
    headless = args.headless.lower() != "false"

    develop_app(
        args.company_name,
        skip_duns=args.no_duns,
        skip_cert=args.no_cert,
        skip_domain=args.no_domain,
        skip_email=args.no_email,
        headless=headless,
        artifact=args.artifact,
    )


if __name__ == "__main__":
    main()