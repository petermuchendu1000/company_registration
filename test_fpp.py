"""Integration test: generate_privacy_policy() for 51 St Margarets Road."""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

from apps.generator.privacy_policy import generate_privacy_policy

app_data = {
    "display_name": "51 St Margarets Road",
    "support_email": "abdulelahhabib060@gmail.com",
    "application_id": "uk.c02591663.shift",
    "company_name": "51 St Margarets Road Management Limited",
    "company_number": "02591663",
}

try:
    url = generate_privacy_policy(app_data)
    print(f"\n=== SUCCESS ===")
    print(f"Privacy Policy URL: {url}")
except Exception as e:
    print(f"\n=== FAILED ===")
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
