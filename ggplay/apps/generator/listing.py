"""Generate Play Console listing and app-content metadata.

These files are a local dossier for human entry in Play Console. They do not
submit anything to Google.
"""

from __future__ import annotations

import json
from pathlib import Path


def short_description(role_noun: str, modules: list[dict] | None = None) -> str:
    if modules:
        labels = ", ".join(module["nav_label"].lower() for module in modules[:3])
        return f"Offline {role_noun.lower()} toolkit for {labels}, notes, and summaries."
    role = role_noun.lower()
    if role == "trip":
        return "Simple trip journal for local mileage, delivery, and activity logs."
    if role == "visit":
        return "Simple visit journal for local site notes and daily activity logs."
    return "Simple shift journal for local work logs and daily handover notes."


def full_description(display_name: str, role_noun: str, export_title: str, modules: list[dict] | None = None) -> str:
    role = role_noun.lower()
    modules = modules or []
    feature_lines = [f"- {module['title']}: {module['short_description']}" for module in modules]
    if not feature_lines:
        feature_lines = [
            f"- Start and end a {role} from a clear dashboard.",
            "- Keep a running count for the current work session.",
            "- Add local notes for handover, delivery, site, or activity context.",
            "- Review a short on-device activity summary during the session.",
            "- Use a clean, company-branded interface built for quick repeated use.",
        ]

    sections = [
        f"{display_name} is a lightweight offline {role} toolkit designed for straightforward daily record keeping on Android.",
        "The app helps users organize practical work activity through a focused set of local tools. It is intentionally minimal: no account, no ads, no network connection, no location access, and no personal data sent off the device.",
        "Key features:\n" + "\n".join(feature_lines),
        f"Privacy:\n{display_name} does not collect, share, or transmit user data. Any notes or counters are kept locally on the device and can be removed by clearing app storage or uninstalling the app.",
        f"Suggested Play category: Business.\nSuggested release title: {export_title} 1.0.",
    ]
    return "\n\n".join(sections)


def listing_payload(company: dict) -> dict:
    display_name = company.get("display_name") or "Work Journal"
    role_noun = company.get("role_noun") or "shift"
    export_title = company.get("export_title") or "Shift Log"
    modules = company.get("modules") or []
    domain = company.get("domain") or ""
    privacy_url = f"https://{domain}/app/privacy.html" if domain else "PENDING_DOMAIN/app/privacy.html"
    website_url = f"https://{domain}" if domain else "PENDING_DOMAIN"

    return {
        "app_identity": {
            "app_name": display_name,
            "package_name": company.get("application_id"),
            "default_language": "English (United Kingdom) - en-GB",
            "app_or_game": "App",
            "free_or_paid": "Free",
            "contains_ads": False,
            "play_category": "Business",
            "tags": ["Productivity", "Business", "Tools"],
        },
        "store_listing": {
            "short_description": short_description(role_noun, modules),
            "full_description": full_description(display_name, role_noun, export_title, modules),
            "privacy_policy_url": privacy_url,
            "developer_email": company.get("support_email"),
            "developer_website": website_url,
            "developer_phone": company.get("organization_phone") or company.get("phone") or "PENDING_ORGANIZATION_PHONE",
        },
        "graphics": {
            "app_icon": "icon-512.png",
            "feature_graphic": "feature-graphic.png",
            "phone_screenshots": [
                "phone-screenshot-1.png",
                "phone-screenshot-2.png",
            ],
            "notes": [
                "Icon must be 512x512 PNG and under 1024KB.",
                "Phone screenshots should be reviewed against the actual built app before upload.",
                "Feature graphic should be reviewed for text legibility before upload.",
            ],
        },
        "app_content": {
            "privacy_policy_required": True,
            "data_safety_required": True,
            "ads": "No ads",
            "app_access": "All functionality is available without login.",
            "target_audience": "Adults/general business users; not designed for children.",
            "content_rating_notes": [
                "No violence.",
                "No sexual content.",
                "No user-generated public content.",
                "No online interaction between users.",
                "No gambling.",
                "No location access.",
            ],
            "permissions": "Zero Android permissions declared in AndroidManifest.xml.",
        },
        "release": {
            "initial_version_code": 1,
            "initial_version_name": "1.0",
            "release_name": "1.0 initial internal test",
            "release_notes": "Initial release with five offline work tools, local notes, recent activity, and privacy-first zero-permission design.",
            "recommended_first_track": "Internal testing",
            "artifact_required_for_play": "AAB",
            "selected_modules": modules,
        },
        "manual_blockers": [
            "Build and verify signed AAB once Android SDK build-tools are installed.",
            "Confirm final developer email is dev@registered-domain after domain setup.",
            "Confirm developer phone number and OTP readiness.",
            "Upload screenshots only after reviewing against a real/emulated build.",
            "Complete Play Console Data safety and Content rating forms manually.",
        ],
    }


def data_safety_payload() -> dict:
    return {
        "collects_user_data": False,
        "shares_user_data": False,
        "data_types_collected": [],
        "data_types_shared": [],
        "data_encryption_in_transit": "Not applicable; app does not transmit user data.",
        "users_can_request_data_deletion": "Not applicable; no server-side user data is collected.",
        "local_data_note": "Session counts and notes stay on device only and can be removed by clearing app storage or uninstalling.",
        "third_party_sdks_collect_data": False,
        "ads_or_analytics_sdks": False,
    }


def write_listing_assets(company: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    listing = listing_payload(company)
    data_safety = data_safety_payload()

    (out_dir / "play_listing.json").write_text(json.dumps(listing, indent=2), encoding="utf-8")
    (out_dir / "data_safety.json").write_text(json.dumps(data_safety, indent=2), encoding="utf-8")

    markdown = [
        f"# {listing['app_identity']['app_name']} Play Listing",
        "",
        f"Package: `{listing['app_identity']['package_name']}`",
        f"Category: {listing['app_identity']['play_category']}",
        f"Short description: {listing['store_listing']['short_description']}",
        "",
        "## Full Description",
        listing["store_listing"]["full_description"],
        "",
        "## Data Safety",
        "- Collects user data: No",
        "- Shares user data: No",
        "- Ads or analytics SDKs: No",
        "- Permissions: Zero declared Android permissions",
        "",
        "## Manual Blockers",
    ]
    markdown.extend(f"- {item}" for item in listing["manual_blockers"])
    (out_dir / "play_listing.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
