"""Intercept D&B lookup API calls to find the backend endpoint."""
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def intercept():
    stealth = Stealth()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await stealth.apply_stealth_async(page)

        api_calls = []

        async def log_request(request):
            url = request.url
            # Capture everything except static assets
            if 'dnb' in url and not any(x in url for x in ['.css', '.js', '.png', '.jpg', '.svg', '.woff', '.gif', 'scene7', 'cookie', 'trustarc', 'analytics', 'google', 'facebook', 'linkedin']):
                info = {
                    'method': request.method,
                    'url': url,
                    'post': request.post_data,
                }
                api_calls.append(info)
                print(f">>> {request.method} {url}")
                if request.post_data:
                    print(f"    BODY: {request.post_data[:500]}")

        async def log_response(response):
            url = response.url
            if 'dnb' in url and not any(x in url for x in ['.css', '.js', '.png', '.jpg', '.svg', '.woff', '.gif', 'scene7', 'cookie', 'trustarc', 'analytics', 'google', 'facebook', 'linkedin']):
                try:
                    body = await response.text()
                    if len(body) < 5000:
                        print(f"<<< {response.status} {url}")
                        print(f"    RESP: {body[:2000]}")
                except Exception:
                    pass

        page.on('request', log_request)
        page.on('response', log_response)

        print("Loading D&B lookup page...")
        await page.goto("https://www.dnb.co.uk/smb/duns/lookup.html", timeout=30000)
        await page.wait_for_timeout(5000)

        # Dump page structure to understand the React app
        print("\n=== Page content analysis ===")
        # Find all buttons
        buttons = await page.locator("button").all()
        for btn in buttons:
            text = await btn.inner_text()
            attrs = await btn.evaluate("el => ({dataCy: el.getAttribute('data-cy'), class: el.className, id: el.id})")
            if text.strip():
                print(f"  Button: '{text.strip()[:60]}' {attrs}")

        # Find all tabs/links with 'registration' or 'search'
        print("\n--- Tabs/Links ---")
        tabs = await page.locator("[role='tab'], [data-cy*='tab'], a[href*='lookup']").all()
        for t in tabs:
            text = await t.inner_text()
            attrs = await t.evaluate("el => ({dataCy: el.getAttribute('data-cy'), role: el.getAttribute('role'), href: el.href})")
            print(f"  Tab: '{text.strip()[:60]}' {attrs}")

        # Find all inputs
        print("\n--- Inputs ---")
        inputs = await page.locator("input").all()
        for inp in inputs:
            attrs = await inp.evaluate("el => ({type: el.type, name: el.name, placeholder: el.placeholder, id: el.id, dataCy: el.getAttribute('data-cy'), value: el.value})")
            print(f"  Input: {attrs}")

        # Dismiss cookie consent
        try:
            consent = page.locator("#truste-consent-button")
            if await consent.count() > 0:
                await consent.click()
                print("Dismissed cookie consent")
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Fill in the company registration number input
        try:
            reg_input = page.locator("input[data-cy='company-registration-number']")
            if await reg_input.count() > 0:
                await reg_input.fill("15046885")
                print("Filled company registration number: 15046885")
                await page.wait_for_timeout(500)

                # Find all buttons to see which is the search/submit
                print("\n--- All buttons after fill ---")
                buttons2 = await page.locator("button").all()
                for b in buttons2:
                    text = (await b.inner_text()).strip()
                    vis = await b.is_visible()
                    attrs = await b.evaluate("el => ({dataCy: el.getAttribute('data-cy'), type: el.type, class: el.className, disabled: el.disabled})")
                    if text and vis:
                        print(f"  Button: '{text[:60]}' visible={vis} {attrs}")

                # Try to find and click search button
                search_selectors = [
                    "button[data-cy*='search']",
                    "button[data-cy*='submit']",
                    "button[data-cy*='lookup']",
                    "button[type='submit']",
                    "button:has-text('Search')",
                    "button:has-text('Look up')",
                    "button:has-text('Find')",
                ]
                clicked = False
                for sel in search_selectors:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        vis = await el.first.is_visible()
                        if vis:
                            await el.first.click()
                            print(f"Clicked: {sel}")
                            clicked = True
                            break

                if not clicked:
                    # Maybe pressing Enter works
                    await reg_input.press("Enter")
                    print("Pressed Enter on input")

                # Wait for API call
                await page.wait_for_timeout(10000)
            else:
                print("Registration number input not found!")
        except Exception as e:
            print(f"Fill/submit error: {e}")

        await page.wait_for_timeout(3000)

        print(f"\n=== Captured {len(api_calls)} API calls ===")
        for c in api_calls:
            print(f"  {c['method']} {c['url']}")
            if c['post']:
                print(f"    POST: {c['post'][:300]}")

        await browser.close()

asyncio.run(intercept())
