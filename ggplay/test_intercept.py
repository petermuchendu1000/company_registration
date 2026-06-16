"""Intercept network requests to discover D&B's actual API endpoints.
Uses the working stealth flow for form interaction, adds route interception."""
import asyncio
import json
import os

from playwright.async_api import async_playwright
from browser_stealth import (
    _create_stealth_context, _get_page, _dismiss_cookies,
    _select_country, _safe_goto, _human_click, _human_type,
    _submit_search, _dismiss_overlays, _rand_delay,
)

SSDIR = os.path.join(os.path.dirname(__file__), "debug_screenshots")
os.makedirs(SSDIR, exist_ok=True)

api_calls = []


async def main():
    async with async_playwright() as pw:
        ctx = await _create_stealth_context(pw, headless=False)
        try:
            page = await _get_page(ctx)

            # Route handler to log all requests but let them through
            async def log_route(route):
                req = route.request
                url = req.url
                # Skip static stuff
                skip = ['.png', '.jpg', '.css', '.woff', '.svg', '.gif', '.ico', '.js',
                        'analytics', 'google-analytics', 'googletagmanager', 'sentry.io',
                        'linkedin', 'facebook', 'infinity-tracking', 'd41.co', 'trustarc']
                if any(s in url.lower() for s in skip):
                    await route.continue_()
                    return

                entry = {"method": req.method, "url": url}
                try:
                    if req.post_data_json:
                        entry["body"] = req.post_data_json
                    elif req.post_data:
                        entry["body_text"] = req.post_data[:500]
                except Exception:
                    pass
                api_calls.append(entry)
                print(f"  >> {req.method} {url}")
                await route.continue_()

            await page.route("**/*", log_route)

            # Navigate
            await _safe_goto(page, "https://www.google.co.uk")
            await asyncio.sleep(2)

            print("[1] Navigate to D&B UK...")
            api_calls.clear()
            await _safe_goto(page, "https://www.dnb.co.uk/duns-number/lookup.html")
            await asyncio.sleep(4)
            await _dismiss_cookies(page)
            await asyncio.sleep(1)

            print(f"\n  Page URL after nav: {page.url}")
            print(f"  Captured {len(api_calls)} non-static requests during page load\n")

            print("[2] Click Registration Number tab...")
            api_calls.clear()
            for sel in ["text=Search by Company Registration Number", "text=Registration Number"]:
                try:
                    tab = page.locator(sel).first
                    if await tab.is_visible(timeout=2000):
                        await _human_click(page, sel)
                        break
                except Exception:
                    continue
            await asyncio.sleep(2)

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
            await asyncio.sleep(1)

            print("\n[5] Click Search Now - WATCHING FOR API CALLS...")
            api_calls.clear()
            await _submit_search(page)
            await asyncio.sleep(5)

            print(f"\n=== API CALLS AFTER SEARCH ({len(api_calls)}) ===")
            for i, call in enumerate(api_calls):
                print(f"  [{i}] {call['method']} {call['url']}")
                if 'body' in call:
                    print(f"       body: {json.dumps(call['body'])[:500]}")
                if 'body_text' in call:
                    print(f"       body: {call['body_text']}")

            await page.screenshot(path=os.path.join(SSDIR, "intercept_results.png"), full_page=True)

            print("\n[6] Click Select button - WATCHING FOR API CALLS...")
            api_calls.clear()
            await _dismiss_overlays(page)
            for sel in ["button:has-text('Select')", "a:has-text('Select')"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        await _dismiss_overlays(page)
                        await el.click(force=True)
                        print(f"  Clicked: {sel}")
                        break
                except Exception as e:
                    print(f"  {sel} failed: {e}")
                    continue

            # Wait and capture
            await asyncio.sleep(8)
            print(f"\n=== API CALLS AFTER SELECT ({len(api_calls)}) ===")
            for i, call in enumerate(api_calls):
                print(f"  [{i}] {call['method']} {call['url']}")
                if 'body' in call:
                    print(f"       body: {json.dumps(call['body'])[:500]}")
                if 'body_text' in call:
                    print(f"       body: {call['body_text']}")

            # Check URL change
            print(f"\n  Current URL: {page.url}")
            print(f"  Open pages: {len(ctx.pages)}")

            await page.screenshot(path=os.path.join(SSDIR, "intercept_after_select.png"), full_page=True)

            # Also dump page text
            try:
                text = await page.inner_text("body")
                print(f"\n=== PAGE TEXT (first 2000) ===")
                print(text[:2000])
            except Exception as e:
                print(f"  Could not get text: {e}")

            await asyncio.sleep(2)
        finally:
            await ctx.close()


asyncio.run(main())
