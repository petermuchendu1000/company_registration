"""Generate per-company install assets: icon, QR, install page, privacy, hashes, manifest.

Outputs placed under pipeline_output/apps/{company_number}/
"""

from __future__ import annotations

import hashlib
import html
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .listing import write_listing_assets

try:
    import qrcode
except ModuleNotFoundError:
    qrcode = None


def _initials(name: str) -> str:
    parts = [p for p in name.replace('-', ' ').split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def make_icon_png(dest: Path, color: str, name: str, size: int = 512) -> None:
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    # Fill background circle with brand primary
    rgb = tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    draw.ellipse([(0, 0), (size - 1, size - 1)], fill=rgb)

    initials = _initials(name)
    try:
        font = ImageFont.truetype("arial.ttf", size // 3)
    except Exception:
        font = ImageFont.load_default()

    try:
        bbox = draw.textbbox((0, 0), initials, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
    except Exception:
        w, h = draw.textsize(initials, font=font)
    draw.text(((size - w) / 2, (size - h) / 2), initials, fill=(255, 255, 255), font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")


def make_qr(dest: Path, url: str, size: int = 512) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if qrcode is not None:
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        img = img.resize((size, size))
        img.save(dest, format="PNG")
        return

    img = Image.new("RGBA", (size, size), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([(16, 16), (size - 17, size - 17)], outline="black", width=4)
    message = "QR dependency missing\nInstall page:\n" + url
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    draw.multiline_text((32, size // 3), message, fill="black", font=font, spacing=8)
    img.save(dest, format="PNG")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def make_install_page(dest: Path, apk_name: str, aab_name: str, company: dict[str, str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    title = html.escape(company.get("display_name", "App") or "App")
    support = html.escape(company.get("support_email", "support@example.uk") or "support@example.uk")
    apk_link = f'<p><a href="{html.escape(apk_name)}">Download APK</a></p>' if apk_name else "<p>APK: not generated</p>"
    aab_line = f"<p>AAB: <code>{html.escape(aab_name)}</code></p>" if aab_name else "<p>AAB: not generated</p>"
    html_doc = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Install {title}</title>
    </head>
    <body>
      <h1>Install {title}</h1>
      <p>Download and install the APK below:</p>
      {apk_link}
      {aab_line}
      <p>APK SHA-256: <code>{company.get('apk_sha256','')}</code></p>
      <p>AAB SHA-256: <code>{company.get('aab_sha256','')}</code></p>
      <p>Support: {support}</p>
    </body>
    </html>
    """
    dest.write_text(html_doc, encoding="utf-8")


def make_privacy_page(dest: Path, company: dict[str, str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    title = html.escape(company.get("display_name", "App") or "App")
    support = html.escape(company.get("support_email", "support@example.uk") or "support@example.uk")
    html_doc = f"""
    <!doctype html>
    <html lang="en">
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Privacy</title></head>
    <body>
    <h1>Privacy policy for {title}</h1>
    <p>This app collects no personal data. It stores user preferences locally on the device.</p>
    <p>Contact: {support}</p>
    </body>
    </html>
    """
    dest.write_text(html_doc, encoding="utf-8")


def write_manifest(dest: Path, metadata: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _font(size: int, bold: bool = False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_text_block(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str], fill, size: int, line_gap: int = 10, bold: bool = False) -> int:
    x, y = xy
    font = _font(size, bold=bold)
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        bbox = draw.textbbox((x, y), line, font=font)
        y += (bbox[3] - bbox[1]) + line_gap
    return y


def _wrap_text(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width=width, break_long_words=False, replace_whitespace=True) or [""]


def make_phone_screenshot(dest: Path, company: dict[str, str], primary_color: str, variant: int) -> None:
    width, height = 1080, 1920
    img = Image.new("RGB", (width, height), "#F7F8FA")
    draw = ImageDraw.Draw(img)
    primary = tuple(int(primary_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    title = company.get("display_name", "Work Journal")
    export_title = company.get("export_title", "Shift Log")
    modules = company.get("modules") or []
    module = modules[(variant - 1) % len(modules)] if modules else {"title": "Work Notes", "short_description": "Local work notes"}

    draw.rounded_rectangle((64, 72, width - 64, 350), radius=18, fill=primary)
    title_lines = _wrap_text(title, 27)[:2]
    y = _draw_text_block(draw, (104, 116), title_lines, "white", 46, line_gap=10, bold=True)
    draw.text((104, y + 8), export_title, fill="white", font=_font(34, bold=True))
    draw.text((104, 294), f"Company No. {company.get('company_number', '')}", fill="white", font=_font(28))

    card_y = 430
    cards = [
        (module.get("metric_label", "Items"), module.get("sample_value", "3")),
        ("Status", "Active" if variant == 1 else "Ready"),
    ]
    for i, (label, value) in enumerate(cards):
        left = 64 + i * 486
        draw.rounded_rectangle((left, card_y, left + 440, card_y + 220), radius=18, fill="white", outline="#E1E4E8", width=2)
        draw.text((left + 34, card_y + 34), label, fill="#4B5563", font=_font(32))
        draw.text((left + 34, card_y + 92), value, fill=primary, font=_font(64, bold=True))

    panel_y = 720
    draw.rounded_rectangle((64, panel_y, width - 64, panel_y + 470), radius=18, fill="white", outline="#E1E4E8", width=2)
    draw.text((104, panel_y + 46), module.get("title", "Work Tool"), fill="#111827", font=_font(44, bold=True))
    summary_lines = _wrap_text(module.get("short_description", "Local work notes"), 39)[:3]
    detail_lines = _wrap_text(module.get("detail", "Keep the workflow organized locally on this device."), 44)[:3]
    y = _draw_text_block(draw, (104, panel_y + 118), summary_lines, "#374151", 34, line_gap=10)
    y += 20
    _draw_text_block(draw, (104, y), detail_lines, "#111827", 30, line_gap=10)

    action_y = 1250
    draw.rounded_rectangle((64, action_y, width - 64, action_y + 148), radius=18, fill=primary)
    button_label = module.get("primary_action", "Start") if variant == 1 else module.get("secondary_action", "Review")
    draw.text((104, action_y + 46), button_label, fill="white", font=_font(40, bold=True))

    draw.rounded_rectangle((64, 1460, width - 64, 1740), radius=18, fill="white", outline="#E1E4E8", width=2)
    _draw_text_block(
        draw,
        (104, 1510),
        ["Privacy-first by design", "No account, no ads, no location, no network permissions."],
        "#111827",
        36,
        line_gap=24,
        bold=variant == 2,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")


def make_feature_graphic(dest: Path, company: dict[str, str], primary_color: str) -> None:
    width, height = 1024, 500
    img = Image.new("RGB", (width, height), "#F7F8FA")
    draw = ImageDraw.Draw(img)
    primary = tuple(int(primary_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    draw.rectangle((0, 0, width, height), fill="#F7F8FA")
    draw.rounded_rectangle((54, 54, width - 54, height - 54), radius=18, fill=primary)
    draw.text((100, 120), company.get("display_name", "Work Journal"), fill="white", font=_font(54, bold=True))
    draw.text((100, 206), company.get("export_title", "Shift Log"), fill="white", font=_font(38))
    draw.text((100, 294), "Local work logs. Clear notes. Zero-permission privacy.", fill="white", font=_font(30))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG")


def backup_keystore(dest_dir: Path, keystore_path: Path, keystore_password: str, alias: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Copy keystore file
    dst = dest_dir / keystore_path.name
    if keystore_path.exists():
        from shutil import copy2
        copy2(keystore_path, dst)
    # Write signing.json with minimal metadata (password is written locally)
    signing = {
        "keystore": dst.name,
        "alias": alias,
        "store_password": keystore_password,
    }
    (dest_dir / "signing.json").write_text(json.dumps(signing, indent=2), encoding="utf-8")


def generate_all(company: dict, apk_path: Path | None, aab_path: Path | None, out_dir: Path, primary_color: str) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # icon
    make_icon_png(out_dir / "icon-512.png", primary_color, company.get("display_name", "App"))
    make_phone_screenshot(out_dir / "phone-screenshot-1.png", company, primary_color, variant=1)
    make_phone_screenshot(out_dir / "phone-screenshot-2.png", company, primary_color, variant=2)
    make_feature_graphic(out_dir / "feature-graphic.png", company, primary_color)
    # qr -> point to /app/install.html on domain if present else to local path
    install_page = "install.html"
    apk_name = Path(apk_path).name if apk_path else ""
    aab_name = Path(aab_path).name if aab_path else ""
    # metadata
    metadata = {
        "generated_at": company.get("generated_at"),
        "display_name": company.get("display_name"),
        "company": company.get("display_name"),
        "legal_company_name": company.get("company_name"),
        "company_number": company.get("company_number"),
        "archetype": company.get("archetype"),
        "modules": company.get("modules") or [],
        "flavor": company.get("flavor"),
        "package": company.get("application_id"),
        "apk": apk_name,
        "aab": aab_name or None,
        "domain": company.get("domain") or "",
        "install_page": install_page,
        "privacy_page": "privacy.html",
    }
    # compute sha
    if apk_path and apk_path.exists():
        metadata["apk_sha256"] = sha256_file(apk_path)
        (out_dir / "apk.sha256").write_text(metadata["apk_sha256"], encoding="utf-8")
    if aab_path and aab_path.exists():
        metadata["aab_sha256"] = sha256_file(aab_path)
        (out_dir / "aab.sha256").write_text(metadata["aab_sha256"], encoding="utf-8")

    metadata.update({"support_email": company.get("support_email"), "application_id": company.get("application_id")})
    write_manifest(out_dir / "manifest.json", metadata)
    write_listing_assets(company, out_dir)

    make_install_page(out_dir / install_page, apk_name, aab_name, metadata)
    make_privacy_page(out_dir / "privacy.html", metadata)
    # QR pointing to install page at domain if provided, else point to local file
    if company.get("domain"):
        url = f"https://{company['domain']}/app/{install_page}"
    else:
        url = install_page
    make_qr(out_dir / "qr.png", url)
