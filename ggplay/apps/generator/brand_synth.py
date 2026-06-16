"""Deterministic brand palette from company name.

No image generation — we only emit a per-flavor colors.xml that overrides
`@color/brand_*` in `main/`. The vector launcher icon picks those up
automatically, so every flavor ends up with a distinct-coloured icon at
zero per-flavor compute and zero PNG disk.
"""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    primary: str          # brand primary — darker, readable on white
    primary_dark: str     # status bar tone
    accent: str           # hand/minute-hand highlight
    background: str       # app background (kept near-white for readability)
    surface: str          # card surfaces
    on_primary: str       # text/ink on primary
    on_background: str    # body text


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(int(r * 255), int(g * 255), int(b * 255))


def _hue_from_name(name: str) -> float:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    hue_deg = int.from_bytes(digest, "big") % 360
    return hue_deg / 360.0


def palette_for(company_name: str) -> Palette:
    h = _hue_from_name(company_name)

    # Primary: vibrant but readable on white (L=32%, S=65%)
    pr, pg, pb = colorsys.hls_to_rgb(h, 0.32, 0.65)
    # Primary dark: same hue, deeper (L=22%)
    dr, dg, db = colorsys.hls_to_rgb(h, 0.22, 0.70)
    # Accent: analogue hue (+30°), brighter (L=48%, S=72%)
    ar, ag, ab = colorsys.hls_to_rgb((h + 30 / 360.0) % 1.0, 0.48, 0.72)

    return Palette(
        primary=_rgb_to_hex(pr, pg, pb),
        primary_dark=_rgb_to_hex(dr, dg, db),
        accent=_rgb_to_hex(ar, ag, ab),
        background="#FAFAFA",
        surface="#FFFFFF",
        on_primary="#FFFFFF",
        on_background="#1C1B1F",
    )


def colors_xml(palette: Palette) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        f'    <color name="brand_primary">{palette.primary}</color>\n'
        f'    <color name="brand_primary_dark">{palette.primary_dark}</color>\n'
        f'    <color name="brand_accent">{palette.accent}</color>\n'
        f'    <color name="brand_background">{palette.background}</color>\n'
        f'    <color name="brand_surface">{palette.surface}</color>\n'
        f'    <color name="brand_on_primary">{palette.on_primary}</color>\n'
        f'    <color name="brand_on_background">{palette.on_background}</color>\n'
        "</resources>\n"
    )


def strings_xml(app_name: str) -> str:
    safe = (
        app_name
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "\\'")
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        f'    <string name="app_name">{safe}</string>\n'
        "</resources>\n"
    )


if __name__ == "__main__":
    import sys
    for name in sys.argv[1:] or ["SWIFT PLUS PERSONNEL LTD", "51 ST MARGARETS ROAD MANAGEMENT LIMITED", "T- QUO LTD"]:
        p = palette_for(name)
        print(f"{name:<45}  primary={p.primary}  accent={p.accent}  dark={p.primary_dark}")
