"""Generate per-company install assets: icon, QR, install page, privacy, hashes, manifest.

Outputs placed under pipeline_output/apps/{company_number}/
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import screenshots


# ── Colour helpers ────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def _darken(rgb: tuple, f: float = 0.30) -> tuple:
    return tuple(max(0, int(c * (1 - f))) for c in rgb)

def _lighten(rgb: tuple, f: float = 0.50) -> tuple:
    return tuple(min(255, int(c + (255 - c) * f)) for c in rgb)

def _company_seed(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)

def _text_center(draw, text: str, font, cx: float, cy: float,
                 fill=(255, 255, 255), shadow=None) -> None:
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        w, h, ox, oy = bb[2]-bb[0], bb[3]-bb[1], bb[0], bb[1]
    except Exception:
        w, h, ox, oy = (*draw.textsize(text, font=font), 0, 0)
    x, y = cx - w / 2 - ox, cy - h / 2 - oy
    if shadow:
        draw.text((x + 2, y + 2), text, fill=shadow, font=font)
    draw.text((x, y), text, fill=fill, font=font)

def _clip_circle(img: Image.Image, size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([(0, 0), (size - 1, size - 1)], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), mask=mask)
    return out


# ── Initials ──────────────────────────────────────────────────────────────────

def _initials(name: str) -> str:
    parts = [p for p in name.replace('-', ' ').split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def make_icon_png(dest: Path, color: str, name: str, size: int = 512,
                  company_number: str = "") -> None:
    """Generate a unique app icon using one of 6 distinct visual styles.

    Style is selected deterministically from the company number hash so
    the same company always produces the same design across rebuilds.

    Styles:
      0 – Solid circle with inner accent ring
      1 – Diagonal two-tone split
      2 – White background + brand rounded-square
      3 – White background + brand hexagon
      4 – Outer ring + filled inner circle + 6 orbital dots
      5 – Light tinted background + oversized initial + bottom bar
    """
    rgb  = _hex_to_rgb(color)
    seed = _company_seed(company_number or name)
    style = seed % 6
    cx = cy = size // 2

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    initials = _initials(name)
    font     = _load_font(size // 3)
    font_xl  = _load_font(int(size * 0.52))

    if style == 0:
        # ── Solid circle with inner accent ring ───────────────────────────────
        draw.ellipse([(0, 0), (size-1, size-1)], fill=rgb)
        lighter = _lighten(rgb, 0.28)
        rr = int(size * 0.37)
        draw.ellipse([(cx-rr, cy-rr), (cx+rr, cy+rr)], fill=lighter)
        ri = int(size * 0.30)
        draw.ellipse([(cx-ri, cy-ri), (cx+ri, cy+ri)], fill=rgb)
        _text_center(draw, initials, font, cx, cy, (255,255,255), shadow=(0,0,0,70))

    elif style == 1:
        # ── Diagonal two-tone split ────────────────────────────────────────────
        dark = _darken(rgb, 0.32)
        draw.polygon([(0,0),(size,0),(0,size)], fill=dark)
        draw.polygon([(size,0),(size,size),(0,size)], fill=rgb)
        lw = max(2, size // 80)
        draw.line([(size, 0), (0, size)], fill=(255,255,255,55), width=lw)
        _text_center(draw, initials, font, cx, cy, (255,255,255), shadow=(0,0,0,90))

    elif style == 2:
        # ── White background + brand rounded-square ────────────────────────────
        draw.ellipse([(0,0),(size-1,size-1)], fill=(255,255,255))
        m = int(size * 0.09)
        draw.rounded_rectangle([(m, m), (size-m, size-m)],
                                radius=int(size * 0.20), fill=rgb)
        _text_center(draw, initials, font, cx, cy, (255,255,255))

    elif style == 3:
        # ── White background + brand hexagon ──────────────────────────────────
        draw.ellipse([(0,0),(size-1,size-1)], fill=(255,255,255))
        r_hex = int(size * 0.41)
        pts = [
            (cx + r_hex * math.cos(math.pi/6 + i * math.pi/3),
             cy + r_hex * math.sin(math.pi/6 + i * math.pi/3))
            for i in range(6)
        ]
        draw.polygon(pts, fill=rgb)
        _text_center(draw, initials, font, cx, cy, (255,255,255))

    elif style == 4:
        # ── Outer ring + inner circle + 6 orbital dots ────────────────────────
        outer = _lighten(rgb, 0.30)
        draw.ellipse([(0,0),(size-1,size-1)], fill=outer)
        ri = int(size * 0.34)
        draw.ellipse([(cx-ri,cy-ri),(cx+ri,cy+ri)], fill=rgb)
        dot_r  = max(3, int(size * 0.040))
        orbit  = int(size * 0.41)
        for i in range(6):
            a  = i * math.pi / 3
            dx = cx + int(orbit * math.cos(a))
            dy = cy + int(orbit * math.sin(a))
            draw.ellipse([(dx-dot_r, dy-dot_r), (dx+dot_r, dy+dot_r)],
                         fill=(255, 255, 255, 200))
        _text_center(draw, initials, font, cx, cy, (255,255,255))

    else:  # style == 5
        # ── Light tint background + oversized initial + bottom accent bar ─────
        tint = _lighten(rgb, 0.86)
        draw.ellipse([(0,0),(size-1,size-1)], fill=tint)
        _text_center(draw, initials[0], font_xl, cx, cy - int(size * 0.06), rgb)
        bar_h = int(size * 0.155)
        draw.rectangle([(0, size - bar_h), (size, size)], fill=rgb)

    result = _clip_circle(canvas, size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result.save(dest, format="PNG")


def _load_font(size: int):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def make_feature_graphic(dest: Path, color: str, name: str,
                          company_number: str = "") -> None:
    """Create a 1024×500 feature graphic (Google Play).

    Four background styles chosen by company number hash:
      0 – Solid colour + fade-to-dark overlay
      1 – Diagonal two-tone split (dark left, primary right)
      2 – Solid colour + repeating dot-grid pattern
      3 – Solid colour + large offset accent circle for depth
    """
    W, H = 1024, 500
    rgb   = _hex_to_rgb(color)
    dark  = _darken(rgb, 0.38)
    seed  = _company_seed(company_number or name)
    style = seed % 4

    img  = Image.new("RGB", (W, H), rgb)
    draw = ImageDraw.Draw(img)

    if style == 0:
        # Fade-to-dark gradient at bottom
        for y in range(H // 2, H):
            alpha = int(55 * (y - H // 2) / (H // 2))
            row = Image.new("RGBA", (W, 1), (0, 0, 0, alpha))
            img.paste(row.convert("RGB"), (0, y), row)

    elif style == 1:
        # Diagonal split: dark top-left triangle, primary bottom-right
        draw.polygon([(0, 0), (W, 0), (0, H)], fill=dark)
        draw.polygon([(W, 0), (W, H), (0, H)], fill=rgb)
        lw = max(3, W // 100)
        draw.line([(W, 0), (0, H)], fill=(255,255,255,40), width=lw)

    elif style == 2:
        # Dot-grid overlay
        dot_r   = max(2, W // 80)
        spacing = W // 14
        for gx in range(0, W + spacing, spacing):
            for gy in range(0, H + spacing, spacing):
                draw.ellipse([(gx-dot_r, gy-dot_r), (gx+dot_r, gy+dot_r)],
                             fill=_lighten(rgb, 0.20))

    else:  # style == 3
        # Large offset circle in lighter tint for depth
        lighter = _lighten(rgb, 0.22)
        cr = int(H * 1.1)
        draw.ellipse([(W - cr - int(H*0.1), H//2 - cr),
                      (W - int(H*0.1) + cr, H//2 + cr)], fill=lighter)

    # Text: company name + tagline centred
    font_lg = _load_font(68)
    font_sm = _load_font(28)

    taglines = [
        "Work Journal  •  Local  •  Private  •  Offline",
        "Simple. Secure. No Account Required.",
        "Record your work. Stay in control.",
        "Business Tools  •  100% Offline  •  No Permissions",
    ]
    tagline = taglines[seed % len(taglines)]

    try:
        bb = draw.textbbox((0, 0), name, font=font_lg)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
    except Exception:
        tw, th = draw.textsize(name, font=font_lg)

    cy = H // 2
    draw.text(((W - tw) / 2, cy - th - 14), name,
              fill=(255,255,255), font=font_lg)

    try:
        bb2 = draw.textbbox((0, 0), tagline, font=font_sm)
        tw2 = bb2[2] - bb2[0]
    except Exception:
        tw2, _ = draw.textsize(tagline, font=font_sm)
    draw.text(((W - tw2) / 2, cy + 14), tagline,
              fill=(255, 255, 255, 180), font=font_sm)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")


# The store screenshots are not drawn here any more. This module used to
# render one hardcoded set of shift-journal screens for every company — a
# timer, a "Log" tab, a calculator — so a mileage or quote app shipped
# pictures of a product it is not. They are now captured from the built app
# by ListingScreenshotTest; apps/generator/screenshots.py runs it and copies
# the results in.


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def make_install_page(dest: Path, apk_name: str, aab_name: str | None, company: dict[str, str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    apk_line = f"<p><a href=\"{apk_name}\">Download APK</a></p>" if apk_name else "<p>APK not generated.</p>"
    aab_line = f"<p>AAB: <code>{aab_name}</code></p>" if aab_name else ""
    aab_sha_line = f"<p>AAB SHA-256: <code>{company.get('aab_sha256','')}</code></p>" if company.get('aab_sha256') else ""
    html = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Install {company.get('display_name','App')}</title>
    </head>
    <body>
      <h1>Install {company.get('display_name','App')}</h1>
      <p>Download and install the APK below:</p>
      {apk_line}
      {aab_line}
      <p>APK SHA-256: <code>{company.get('apk_sha256','')}</code></p>
      {aab_sha_line}
      <p>Support: {company.get('support_email','support@example.uk')}</p>
    </body>
    </html>
    """
    dest.write_text(html, encoding="utf-8")


def make_privacy_page(dest: Path, company: dict[str, str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    website = company.get('domain')
    website_line = f"<p>Website: https://{website}</p>" if website else ""
    html = f"""
    <!doctype html>
    <html lang="en">
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Privacy</title></head>
    <body>
    <h1>Privacy policy for {company.get('display_name','App')}</h1>
    <p>This app collects no personal data. It stores user preferences locally on the device.</p>
    <p>Contact: {company.get('support_email','support@example.uk')}</p>
    {website_line}
    </body>
    </html>
    """
    dest.write_text(html, encoding="utf-8")


def short_description(role_noun: str | None = None, archetype_id: str = "shift") -> str:
    role = (role_noun or "shift").lower()
    if archetype_id == "mileage":
        # Stays inside Play's 80-character limit for the longest role noun
        # the mileage archetype uses ("delivery run").
        return f"Offline {role} log: odometer readings and HMRC approved mileage totals."
    if role == "visit":
        return "Simple visit journal for local site notes and daily activity logs."
    return "Simple shift journal for local work logs and daily handover notes."


def full_description(
    display_name: str,
    role_noun: str | None = None,
    export_title: str | None = None,
    archetype_id: str = "shift",
) -> str:
    """Store description for the app that was actually built.

    The feature list has to match the archetype: describing a mileage log as a
    one-tap timer is a Play metadata violation as well as being untrue.
    """
    role = (role_noun or "shift").lower()
    export_title = export_title or "Shift Log"

    if archetype_id == "mileage":
        opening = (
            f"{display_name} is an offline mileage log for drivers who need a clean "
            f"record of business travel on Android."
        )
        feature_lines = [
            f"- Record each {role} as an opening and closing odometer reading; the app works out the distance.",
            "- Opening reading is carried over from your last journey so consecutive trips line up.",
            "- Running tax-year total applies HMRC approved mileage rates, 45p for the first 10,000 miles and 25p after.",
            f"- {export_title} groups journeys by month with per-month mileage subtotals.",
            "- Add a purpose to any journey for invoicing or expense context.",
            "- Branded interface with company details and zero-permission local storage.",
        ]
    else:
        opening = (
            f"{display_name} is a lightweight offline {role} toolkit designed for "
            f"straightforward daily record keeping on Android."
        )
        feature_lines = [
            f"- Start and end a {role} from the dashboard with one tap.",
            f"- Add a short note to each {role} for handover, delivery, or site context.",
            "- Live session counter tracks how many sessions you have completed today.",
            f"- Review recent {role} activity including start time, end time, and duration.",
            "- Branded interface with company name and zero-permission local storage.",
        ]

    sections = [
        opening,
        "The app helps users organise practical work activity through a focused set of local tools. It is intentionally minimal: no account, no ads, no network connection, no location access, and no personal data sent off the device.",
        "Key features:\n" + "\n".join(feature_lines),
        f"Privacy:\n{display_name} does not collect, share, or transmit user data. Any notes or session records are kept locally on the device and can be removed by clearing app storage or uninstalling the app.",
    ]
    return "\n\n".join(sections)


def _truncate_title(name: str) -> str:
    """Google Play app title: max 30 characters."""
    if len(name) <= 30:
        return name
    truncated = name[:27].rstrip() + "..."
    print(f"  WARN: app title '{name}' ({len(name)} chars) exceeds 30-char limit — truncated to '{truncated}'")
    return truncated


def _validate_short_desc(text: str) -> str:
    """Google Play short description: max 80 characters."""
    if len(text) <= 80:
        return text
    truncated = text[:77].rstrip() + "..."
    print(f"  WARN: short description ({len(text)} chars) exceeds 80-char limit — truncated")
    return truncated


def listing_payload(company: dict[str, str]) -> dict:
    display_name = company.get("display_name") or "Work Journal"
    role_noun = company.get("role_noun") or "shift"
    export_title = company.get("export_title") or "Shift Log"
    domain = company.get("domain") or ""
    # Use the live freeprivacypolicy.com URL if already generated; fall back to domain-based path
    privacy_url = (
        company.get("privacy_policy_url")
        or (f"https://{domain}/app/privacy.html" if domain else "PENDING_PRIVACY_POLICY_URL")
    )
    website_url = f"https://{domain}" if domain else "PENDING_DOMAIN"

    from . import archetypes as _archetypes
    archetype = _archetypes.get(company.get("archetype") or _archetypes.DEFAULT_ID)
    shots: dict = company.get("screenshots") or {}

    app_title = _truncate_title(display_name)
    short_desc = _validate_short_desc(short_description(role_noun, archetype.id))

    return {
        "app_identity": {
            "app_name": app_title,
            "package_name": company.get("application_id"),
            "default_language": "English (United Kingdom) - en-GB",
            "app_or_game": "App",
            "free_or_paid": "Free",
            "contains_ads": False,
            "play_category": "Business",
            "tags": ["Productivity", "Business", "Tools"],
        },
        "store_listing": {
            "short_description": short_desc,
            "full_description": full_description(display_name, role_noun, export_title, archetype.id),
            "privacy_policy_url": privacy_url,
            "developer_email": company.get("support_email"),
            "developer_website": website_url,
            "developer_phone": company.get("organization_phone") or "PENDING_ORGANIZATION_PHONE",
        },
        "graphics": {
            "app_icon": "graphics/icon-512.png",
            "feature_graphic": "graphics/feature-graphic.png",
            # What was actually rendered and copied in, not a fixed count of
            # filenames. The old list claimed four of each size whether or
            # not they existed, so a failed render still produced a listing
            # that looked complete.
            "phone_screenshots": shots.get("phone", []),
            "tablet_7inch_screenshots": shots.get("tablet7", []),
            "tablet_10inch_screenshots": shots.get("tablet10", []),
            "screenshot_screens": list(archetype.screenshot_screens),
            "screenshot_provenance": company.get("screenshot_provenance") or {},
            "specs": {
                "icon": "512x512 px, 32-bit PNG with alpha, max 1024 KB",
                "feature_graphic": "1024x500 px, JPEG or 24-bit PNG (no alpha)",
                "phone_screenshots": "1080x1920 px, 24-bit PNG (no alpha), max 8 MB each, min 2 / max 8",
                "tablet_7inch_screenshots": "1200x1920 px, 24-bit PNG (no alpha), min 4 / max 8 — mandatory",
                "tablet_10inch_screenshots": "1600x2560 px, 24-bit PNG (no alpha), min 4 / max 8 — mandatory",
            },
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
            ],
            "permissions": "Zero Android permissions declared in AndroidManifest.xml.",
        },
        "release": {
            "initial_version_code": 1,
            "initial_version_name": "1.0",
            "release_name": "1.0 initial internal test",
            # What this app actually ships, from apps/generator/archetypes.py.
            # The policy gate reads these to check the catalog is not one
            # screen set wearing different colours.
            "archetype": archetype.id,
            "selected_modules": list(archetype.modules),
            "release_notes": "Initial release with offline work tools, local notes, recent activity, and privacy-first zero-permission design.",
            "recommended_first_track": "Internal testing",
            "artifact_required_for_play": "AAB",
        },
        "manual_blockers": [
            "Build and verify signed AAB once Android SDK build-tools are installed.",
            "Set up dev@{domain} email in Namecheap, update Excel support_email, rebuild app.",
            "Confirm developer phone number and OTP readiness.",
            "Upload screenshots only after reviewing against a real/emulated build.",
            "Complete Play Console Data safety section manually (required even for zero-data apps).",
            "Complete IARC content rating questionnaire in Play Console (mandatory before publish).",
            "Verify privacy policy URL is live and publicly accessible worldwide before submission.",
        ],
    }


def data_safety_payload() -> dict[str, object]:
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


def write_listing_assets(company: dict[str, str], out_dir: Path) -> None:
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
        listing['store_listing']['full_description'],
        "",
        "## Data Safety",
        "- Collects user data: No",
        "- Shares user data: No",
        "- Ads or analytics SDKs: No",
        "- Permissions: Zero declared Android permissions",
        "",
        "## Manual Blockers",
    ]
    markdown.extend(f"- {item}" for item in listing['manual_blockers'])
    (out_dir / "play_listing.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def write_manifest(dest: Path, metadata: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def backup_keystore(dest_dir: Path, keystore_path: Path, keystore_password: str, alias: str) -> None:
    signing_dir = dest_dir / "signing"
    signing_dir.mkdir(parents=True, exist_ok=True)
    dst = signing_dir / keystore_path.name
    if keystore_path.exists():
        from shutil import copy2
        copy2(keystore_path, dst)
    signing = {
        "keystore": dst.name,
        "alias": alias,
        "store_password": keystore_password,
    }
    (signing_dir / "signing.json").write_text(json.dumps(signing, indent=2), encoding="utf-8")


def generate_all(company: dict, apk_path: Path | None, aab_path: Path | None,
                 out_dir: Path, primary_color: str,
                 app_dir: str | Path | None = None, flavor: str = "") -> None:
    """Generate all pipeline output assets into a structured tree:

    {out_dir}/
      artifacts/          signed APK + AAB copies
      listing/            play_listing.json, data_safety.json, play_listing.md
      graphics/           icon-512.png, feature-graphic.png, screenshots, qr.png
      web/                install.html, privacy.html
      signing/            keystore + signing.json (written by backup_keystore)
      manifest.json       top-level summary of all artifacts
      apk.sha256
      aab.sha256
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale flat-layout files from earlier pipeline versions
    _STALE_ROOT_NAMES = {
        "icon-512.png", "install.html", "privacy.html", "qr.png",
        "play_listing.json", "play_listing.md", "data_safety.json",
        "signing.json",
    }
    for item in out_dir.iterdir():
        if item.is_file() and (item.name in _STALE_ROOT_NAMES or item.suffix in {".jks", ".apk", ".aab"}):
            item.unlink()

    artifacts_dir = out_dir / "artifacts"
    listing_dir   = out_dir / "listing"
    graphics_dir  = out_dir / "graphics"
    web_dir       = out_dir / "web"
    for d in (artifacts_dir, listing_dir, graphics_dir, web_dir):
        d.mkdir(parents=True, exist_ok=True)

    display_name = company.get("display_name", "App")
    apk_name = Path(apk_path).name if apk_path else ""
    aab_name = Path(aab_path).name if aab_path else None

    # Note: artifacts are placed in artifacts/ by collect_artifacts before this is called.

    # --- Hashes ---
    apk_sha = ""
    aab_sha = ""
    if apk_path and apk_path.exists():
        apk_sha = sha256_file(apk_path)
        (out_dir / "apk.sha256").write_text(apk_sha, encoding="utf-8")
    if aab_path and aab_path.exists():
        aab_sha = sha256_file(aab_path)
        (out_dir / "aab.sha256").write_text(aab_sha, encoding="utf-8")

    # --- Privacy policy (freeprivacypolicy.com) ---
    cn = company.get("company_number", "")
    support_email = company.get("support_email")

    # Reuse existing URL — avoid creating a duplicate policy on every run
    policy_url = None
    existing_manifest = out_dir / "manifest.json"
    if existing_manifest.exists():
        try:
            existing_data = json.loads(existing_manifest.read_text(encoding="utf-8"))
            policy_url = existing_data.get("privacy_policy_url") or None
            if policy_url:
                print(f"  [{cn}] Privacy policy URL (existing): {policy_url}")
        except Exception:
            pass

    if not policy_url and support_email:
        try:
            from .privacy_policy import generate_privacy_policy
            print(f"  [{cn}] Generating privacy policy via freeprivacypolicy.com…")
            policy_url = generate_privacy_policy({
                "display_name": display_name,
                "support_email": support_email,
                "company_name": company.get("company_name") or display_name,
                "application_id": company.get("application_id"),
                "company_number": cn,
            })
            print(f"  [{cn}] Privacy policy URL: {policy_url}")
        except Exception as e:
            print(f"  [{cn}] Privacy policy generation failed: {e}")

    # Screenshots before the listing, not after: the listing manifest names
    # the files, and it used to name four of each size unconditionally while
    # a separate step drew them. Collect first, then write a manifest that
    # describes what is actually on disk.
    screenshot_groups, screenshot_provenance = ({}, {})
    if app_dir and flavor:
        screenshot_groups, screenshot_provenance = screenshots.collect(
            app_dir, flavor, graphics_dir)

    metadata = {
        "display_name": display_name,
        "package": company.get("application_id"),
        # Carried through because listing_payload needs them and silently
        # fell back to the default archetype without them: every listing,
        # mileage and quote included, described a shift journal.
        "archetype": company.get("archetype"),
        "sic_codes": company.get("sic_codes"),
        "screenshots": screenshot_groups,
        "screenshot_provenance": screenshot_provenance,
        "application_id": company.get("application_id"),
        "domain": company.get("domain"),
        "support_email": company.get("support_email"),
        "role_noun": company.get("role_noun"),
        "export_title": company.get("export_title"),
        "organization_phone": company.get("organization_phone"),
        "apk": f"artifacts/{apk_name}" if apk_name else None,
        "aab": f"artifacts/{aab_name}" if aab_name else None,
        "apk_sha256": apk_sha or None,
        "aab_sha256": aab_sha or None,
        "privacy_policy_url": policy_url,
    }

    # --- manifest.json (root summary) ---
    write_manifest(out_dir / "manifest.json", metadata)

    # --- Listing assets (privacy_policy_url flows in via metadata) ---
    write_listing_assets(metadata, listing_dir)

    # --- Graphics ---
    make_icon_png(graphics_dir / "icon-512.png", primary_color, display_name,
                  company_number=cn)
    make_feature_graphic(graphics_dir / "feature-graphic.png", primary_color, display_name,
                         company_number=cn)

    # --- Web pages ---
    make_install_page(web_dir / "install.html", apk_name, aab_name, metadata)
    make_privacy_page(web_dir / "privacy.html", metadata)
