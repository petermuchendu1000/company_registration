"""Quick test: stealth D&B UK DUNS lookup with debug screenshots."""
import json
import logging
import asyncio
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from playwright.async_api import async_playwright
from browser_stealth import (
    _create_stealth_context, _get_page, _dismiss_cookies,
    _select_country, _safe_goto, _human_click, _human_type,
    _submit_search, _dismiss_overlays, _select_and_request_duns,
    _rand_delay,
)

SSDIR = os.path.join(os.path.dirname(__file__), "debug_screenshots")
os.makedirs(SSDIR, exist_ok=True)

TEST_EMAIL = "test@example.com"  # Use a temp email in production


async def debug_lookup():
    async with async_playwright() as pw:
        ctx = await _create_stealth_context(pw, headless=False)
        try:
            page = await _get_page(ctx)
            await _safe_goto(page, "https://www.google.co.uk")
            await asyncio.sleep(2)

            print("[1] Navigating to D&B UK...")
            await _safe_goto(page, "https://www.dnb.co.uk/duns-number/lookup.html")
            await asyncio.sleep(3)
            await _dismiss_cookies(page)
            await asyncio.sleep(1)
            await page.screenshot(path=os.path.join(SSDIR, "01_initial.png"), full_page=True)

            print("[2] Click Registration Number tab...")
            for sel in ["text=Search by Company Registration Number", "text=Registration Number"]:
                try:
                    tab = page.locator(sel).first
                    if await tab.is_visible(timeout=2000):
                        await _human_click(page, sel)
                        break
                except Exception:
                    continue
            await asyncio.sleep(1.5)

            print("[3] Select country...")
            await _select_country(page)
            await asyncio.sleep(1)

            print("[4] Type registration number...")
            for sel in ["input[name*='registration' i]", "input[type='text']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await _human_type(page, sel, "13510663")
                        break
                except Exception:
                    continue
            await asyncio.sleep(0.5)

            print("[5] Click Search Now...")
            await _submit_search(page)
            await asyncio.sleep(4)
            await page.screenshot(path=os.path.join(SSDIR, "05_results.png"), full_page=True)

            print("[6] Click Select + fill form...")
            try:
                result = await asyncio.wait_for(
                    _select_and_request_duns(
                        page,
                        email_address=TEST_EMAIL,
                        first_name="John",
                        last_name="Smith",
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                print("  !! Timed out after 30s")
                await page.screenshot(path=os.path.join(SSDIR, "06_timeout.png"), full_page=True)
                result = {"found": False, "message": "timed out"}

            print(json.dumps(result, indent=2, default=str))

            # Final screenshot
            await page.screenshot(path=os.path.join(SSDIR, "08_final.png"), full_page=True)
            await asyncio.sleep(3)
        finally:
            await ctx.close()


asyncio.run(debug_lookup())
