"""
Stealth Browser Automation for D&B DUNS Lookups
Uses Playwright with aggressive anti-detection to bypass bot protections.

Two main flows:
  1. D&B UK lookup by Company Registration Number (instant if company exists)
  2. D&B "I'm a Google Developer" application submission (fallback)
"""

import asyncio
import json
import random
import time
import re
import os
import logging
from datetime import datetime

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

# Singleton stealth instance with all evasions enabled
_stealth = Stealth()

# ---------------------------------------------------------------------------
# Stealth configuration & human-like constants
# ---------------------------------------------------------------------------

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_LOCALES = ["en-GB", "en-US"]
_TIMEZONES = ["Europe/London", "Europe/Dublin"]

# Persistence directory for cookies / local storage etc.
_PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profiles")


def _rand_delay(low=0.3, high=1.2):
    """Return a random delay in seconds, lognormal-ish."""
    return random.uniform(low, high)


# ---------------------------------------------------------------------------
# Core: launch a stealth browser context
# ---------------------------------------------------------------------------

async def _create_stealth_context(playwright, headless=True):
    """
    Launch Chromium with maximal anti-fingerprint measures:
      - playwright-stealth v2 patches (navigator, webdriver, chrome, permissions, etc.)
      - randomised viewport, locale, timezone, user-agent
      - persistent profile for cookie jar reuse across runs
      - extra args to disable automation flags & force HTTP/1.1
    """
    viewport = random.choice(_VIEWPORTS)
    ua = random.choice(_USER_AGENTS)
    locale = random.choice(_LOCALES)
    tz = random.choice(_TIMEZONES)

    os.makedirs(_PROFILE_DIR, exist_ok=True)

    browser = await playwright.chromium.launch_persistent_context(
        user_data_dir=_PROFILE_DIR,
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-site-isolation-trials",
            "--disable-http2",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-extensions",
            "--disable-popup-blocking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-dev-shm-usage",
            "--enable-features=NetworkService,NetworkServiceInProcess",
            f"--window-size={viewport['width']},{viewport['height']}",
        ],
        ignore_default_args=["--enable-automation"],
        viewport=viewport,
        user_agent=ua,
        locale=locale,
        timezone_id=tz,
        color_scheme="light",
        java_script_enabled=True,
        bypass_csp=False,
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": f"{locale},{locale.split('-')[0]};q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    # Apply stealth patches to all existing pages
    for page in browser.pages:
        try:
            await _stealth.apply_stealth_async(page)
        except Exception:
            pass

    return browser


async def _get_page(context):
    """Get or create a page with stealth applied."""
    if context.pages:
        page = context.pages[0]
    else:
        page = await context.new_page()
    try:
        await _stealth.apply_stealth_async(page)
    except Exception:
        pass
    return page


# ---------------------------------------------------------------------------
# Human-like interaction helpers
# ---------------------------------------------------------------------------

async def _human_type(page, selector, text, delay_range=(40, 120)):
    """Type text character-by-character with random inter-key delays."""
    el = page.locator(selector)
    await el.click()
    await asyncio.sleep(_rand_delay(0.2, 0.5))
    # Clear existing value
    await el.fill("")
    await asyncio.sleep(_rand_delay(0.1, 0.3))
    for ch in text:
        await el.press_sequentially(ch, delay=random.randint(*delay_range))
    await asyncio.sleep(_rand_delay(0.2, 0.6))


async def _human_click(page, selector, move_first=True):
    """Click an element with a random small delay and optional mouse-move."""
    el = page.locator(selector)
    if move_first:
        box = await el.bounding_box()
        if box:
            # Move to a random point inside the element
            x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
            y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await asyncio.sleep(_rand_delay(0.05, 0.2))
    await el.click()
    await asyncio.sleep(_rand_delay(0.3, 0.8))


async def _random_mouse_movements(page, count=3):
    """Perform random mouse movements to mimic human behaviour."""
    vp = page.viewport_size or {"width": 1280, "height": 800}
    for _ in range(count):
        x = random.randint(100, vp["width"] - 100)
        y = random.randint(100, vp["height"] - 100)
        await page.mouse.move(x, y, steps=random.randint(8, 20))
        await asyncio.sleep(_rand_delay(0.1, 0.4))


async def _random_scroll(page, direction="down", amount=None):
    """Scroll the page a random amount."""
    if amount is None:
        amount = random.randint(100, 400)
    delta = amount if direction == "down" else -amount
    await page.mouse.wheel(0, delta)
    await asyncio.sleep(_rand_delay(0.3, 0.8))


async def _wait_for_load(page, timeout=10000):
    """Wait for the page to finish loading. Avoids networkidle which
    hangs on pages with persistent connections (chat widgets etc)."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass


async def _dismiss_cookies(page):
    """Try to dismiss cookie consent banners (common on D&B sites)."""
    cookie_selectors = [
        "button:has-text('Agree')",
        "button:has-text('Accept')",
        "button:has-text('Agree & Proceed')",
        "button:has-text('Required Only')",
        "[id*='cookie'] button",
        "[class*='cookie'] button:has-text('Accept')",
        "[id*='consent'] button",
        "#truste-consent-button",
        ".trustarc-agree-btn",
    ]
    for sel in cookie_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                await asyncio.sleep(_rand_delay(0.5, 1.5))
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# FLOW 1: D&B UK Lookup by Company Registration Number
# ---------------------------------------------------------------------------

DNB_UK_LOOKUP_URL = "https://www.dnb.co.uk/smb/duns/lookup.html"
DNB_UK_LOOKUP_ALT = "https://www.dnb.co.uk/duns-number/lookup.html"  # old URL, redirects to above


async def _safe_goto(page, url, retries=3):
    """Navigate to a URL with retries and fallback wait strategies."""
    wait_strategies = ["domcontentloaded", "commit", "load"]
    for attempt in range(retries):
        strategy = wait_strategies[min(attempt, len(wait_strategies) - 1)]
        try:
            await page.goto(url, wait_until=strategy, timeout=30000)
            return True
        except Exception as e:
            logger.warning(f"Navigation attempt {attempt+1} ({strategy}) failed: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(_rand_delay(2.0, 5.0))
    return False


async def _dnb_uk_lookup(context, company_number, company_name="", post_town="",
                         post_code="", email_address="", first_name="", last_name=""):
    """
    Search D&B UK for a DUNS number using company registration number.
    Falls back to D&B US lookup page if UK site blocks.
    Returns dict with results or None.
    """
    page = await _get_page(context)

    # Try UK site first, then US site
    lookup_urls = [
        DNB_UK_LOOKUP_URL,
        "https://www.dnb.com/duns/get-a-duns.html",
    ]

    for url in lookup_urls:
        try:
            logger.info(f"Navigating to D&B lookup: {url}")

            if not await _safe_goto(page, url):
                logger.warning(f"Could not load {url}, trying next...")
                continue

            await asyncio.sleep(_rand_delay(2.0, 4.0))

            # Dismiss cookies
            await _dismiss_cookies(page)
            await asyncio.sleep(_rand_delay(1.0, 2.0))

            # Random mouse movement to look human
            await _random_mouse_movements(page, count=random.randint(2, 4))

            # Try the "Search by Company Registration Number" tab first
            reg_tab_found = False
            reg_tab_selectors = [
                "text=Search by Company Registration Number",
                "text=Registration Number",
                "[data-tab*='registration']",
                "a:has-text('Registration')",
                "button:has-text('Registration')",
                "li:has-text('Registration Number')",
            ]
            for sel in reg_tab_selectors:
                try:
                    tab = page.locator(sel).first
                    if await tab.is_visible(timeout=2000):
                        await _human_click(page, sel)
                        reg_tab_found = True
                        logger.info("Clicked 'Search by Registration Number' tab")
                        await asyncio.sleep(_rand_delay(1.0, 2.0))
                        break
                except Exception:
                    continue

            if reg_tab_found:
                # === Registration number search ===
                result = await _search_by_registration(
                    page, company_number, email_address, first_name, last_name
                )
                if result and result.get("found"):
                    return result

            # Fallback: search by company name
            logger.info("Falling back to company name search")
            # Click back to "Search by Company Name" tab if needed
            name_tab_selectors = [
                "text=Search by Company Name",
                "text=Company Name",
                "[data-tab*='name']",
                "a:has-text('Company Name')",
            ]
            for sel in name_tab_selectors:
                try:
                    tab = page.locator(sel).first
                    if await tab.is_visible(timeout=2000):
                        await _human_click(page, sel)
                        await asyncio.sleep(_rand_delay(1.0, 2.0))
                        break
                except Exception:
                    continue

            if company_name:
                result = await _search_by_name(
                    page, company_name, post_town, post_code,
                    email_address, first_name, last_name
                )
                if result and result.get("found"):
                    return result

        except Exception as e:
            logger.error(f"D&B lookup error for {url}: {e}")
            continue

    return {"found": False, "source": "dnb_uk_browser", "message": "No DUNS found via D&B lookup"}


async def _select_country(page, country_label="United Kingdom", country_value="GB"):
    """Select the country dropdown on D&B lookup forms."""
    country_selectors = [
        "select[name*='country' i]",
        "select[id*='country' i]",
        "select[class*='country' i]",
        "select",
    ]
    for sel in country_selectors:
        try:
            elements = page.locator(sel)
            count = await elements.count()
            for i in range(count):
                el = elements.nth(i)
                if await el.is_visible(timeout=1500):
                    # Try label first, then value
                    try:
                        await el.select_option(label=country_label)
                        logger.info(f"Selected country by label: {country_label}")
                        await asyncio.sleep(_rand_delay(0.5, 1.0))
                        return True
                    except Exception:
                        pass
                    try:
                        await el.select_option(value=country_value)
                        logger.info(f"Selected country by value: {country_value}")
                        await asyncio.sleep(_rand_delay(0.5, 1.0))
                        return True
                    except Exception:
                        pass
                    # Try partial match labels
                    for label in ["United Kingdom", "UK", "Great Britain", "GB"]:
                        try:
                            await el.select_option(label=label)
                            logger.info(f"Selected country by label: {label}")
                            await asyncio.sleep(_rand_delay(0.5, 1.0))
                            return True
                        except Exception:
                            continue
        except Exception:
            continue
    logger.warning("Could not find/select country dropdown")
    return False


async def _search_by_registration(page, company_number, email_address="",
                                   first_name="", last_name=""):
    """Fill in and submit the registration number search form.
    
    After search results appear, clicks Select and fills the email form
    so D&B will email the DUNS number to the provided address.
    """
    # MUST select country first
    await _select_country(page)
    await asyncio.sleep(_rand_delay(0.5, 1.0))

    # Look for the registration number input
    reg_input_selectors = [
        "input[name*='registration' i]",
        "input[placeholder*='registration' i]",
        "input[id*='registration' i]",
        "input[name*='companyReg' i]",
        "input[name*='regNumber' i]",
        "input[aria-label*='registration' i]",
        "input[type='text']",
    ]

    input_found = False
    for sel in reg_input_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await _human_type(page, sel, company_number)
                input_found = True
                logger.info(f"Typed registration number: {company_number}")
                break
        except Exception:
            continue

    if not input_found:
        return {"found": False, "message": "Could not find registration number input"}

    # Submit search
    await _submit_search(page)

    # Click the Select button using the stable data-cy attribute
    return await _select_and_request_duns(page, email_address, first_name, last_name)


async def _search_by_name(page, company_name, post_town="", post_code="",
                          email_address="", first_name="", last_name=""):
    """Fill in and submit the company name search form."""
    await _random_mouse_movements(page, count=2)

    # Select country
    await _select_country(page)
    await asyncio.sleep(_rand_delay(0.5, 1.0))

    # Company name input
    name_selectors = [
        "input[name*='companyName']",
        "input[name*='Company']",
        "input[name*='company']",
        "input[placeholder*='Company']",
        "input[placeholder*='company']",
        "input[id*='company']",
    ]
    for sel in name_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await _human_type(page, sel, company_name)
                logger.info(f"Typed company name: {company_name}")
                break
        except Exception:
            continue

    # Post town (optional)
    if post_town:
        town_selectors = [
            "input[name*='postTown']",
            "input[name*='town']",
            "input[name*='Town']",
            "input[name*='city']",
            "input[placeholder*='Town']",
        ]
        for sel in town_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1500):
                    await _human_type(page, sel, post_town)
                    break
            except Exception:
                continue

    # Post code (optional)
    if post_code:
        pc_selectors = [
            "input[name*='postCode']",
            "input[name*='Post']",
            "input[name*='postal']",
            "input[placeholder*='Post']",
            "input[placeholder*='Postcode']",
        ]
        for sel in pc_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1500):
                    await _human_type(page, sel, post_code)
                    break
            except Exception:
                continue

    await _submit_search(page)
    return await _select_and_request_duns(page, email_address, first_name, last_name)


async def _dismiss_overlays(page):
    """Dismiss D&B's AI Assistant chat widget if it's blocking clicks.
    Uses narrow selectors to avoid accidentally closing search results."""
    overlay_selectors = [
        # D&B AI Assistant / Qualified chat widget (iframe-based)
        "iframe[title*='chat' i]",
        "[class*='qualified'] button[class*='close' i]",
        "[id*='qualified'] button",
        # Chat minimize button (inside the widget frame)
        "[class*='chat-widget'] button[class*='minimize' i]",
        "[class*='chat-widget'] button[class*='close' i]",
    ]
    for sel in overlay_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=500):
                await el.click(force=True)
                await asyncio.sleep(_rand_delay(0.2, 0.4))
                logger.info(f"Dismissed overlay: {sel}")
        except Exception:
            continue


async def _submit_search(page):
    """Find and click the search/submit button."""
    await asyncio.sleep(_rand_delay(0.5, 1.0))

    submit_selectors = [
        "button:has-text('Search Now')",
        "button:has-text('Search now')",
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Search')",
        "button:has-text('Find')",
        "button:has-text('Lookup')",
        "button:has-text('Look up')",
        "a:has-text('Search Now')",
        ".search-button",
        "[class*='submit']",
    ]
    for sel in submit_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                # Scroll element into view first
                await el.scroll_into_view_if_needed()
                await asyncio.sleep(_rand_delay(0.2, 0.5))
                # Use force click to bypass any overlapping elements
                await el.click(force=True)
                logger.info(f"Clicked submit button: {sel}")
                break
        except Exception:
            continue

    # Wait for results to render
    await asyncio.sleep(_rand_delay(3.0, 5.0))


async def _select_and_request_duns(page, email_address="", first_name="", last_name=""):
    """Click the Select button on a search result, then fill the email form.
    
    D&B doesn't show the DUNS directly - after clicking Select, a form
    asks for name + email and D&B emails the DUNS number to that address.
    """
    await asyncio.sleep(_rand_delay(1.0, 2.0))

    # --- Step 1: Click Select using the stable data-cy selector ---
    select_selectors = [
        "button[data-cy='duns-lookup-uk-selection-continue']",
        "button:has-text('Select')",
        "a:has-text('Select')",
    ]
    clicked = False
    for sel in select_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=5000):
                await el.scroll_into_view_if_needed()
                await asyncio.sleep(_rand_delay(0.3, 0.6))
                await el.click(force=True)
                clicked = True
                logger.info(f"Clicked Select via: {sel}")
                break
        except Exception:
            continue

    if not clicked:
        return {"found": False, "source": "dnb_uk_browser",
                "message": "Search returned results but could not click Select"}

    # Wait for the email form to appear
    await asyncio.sleep(_rand_delay(2.0, 4.0))

    # --- Step 2: Fill the DUNS request form ---
    form_sel = "form[data-cy='duns-lookup-uk-form']"
    try:
        await page.wait_for_selector(form_sel, timeout=10000)
        logger.info("DUNS email form appeared")
    except Exception:
        # Form might not have the data-cy, look for the heading instead
        try:
            await page.wait_for_selector("text=Receive a D", timeout=10000)
            logger.info("DUNS form heading detected")
        except Exception:
            logger.warning("DUNS email form did not appear after clicking Select")
            # Take debug screenshot
            try:
                ss = os.path.join(os.path.dirname(__file__), "debug_screenshots", "06_no_form.png")
                os.makedirs(os.path.dirname(ss), exist_ok=True)
                await page.screenshot(path=ss, full_page=True)
            except Exception:
                pass
            return {"found": False, "source": "dnb_uk_browser",
                    "message": "Select clicked but email form did not appear"}

    if not email_address:
        # No email provided - we found the company but can't request the DUNS
        logger.info("Company found on D&B but no email provided for DUNS request")
        return {"found": True, "source": "dnb_uk_browser",
                "form_ready": True, "duns_emailed": False,
                "message": "Company found. Provide email to receive DUNS."}

    # Fill first name
    if first_name:
        try:
            fn = page.locator("input[data-cy='duns-lookup-form-first-name']")
            await fn.fill(first_name)
            await asyncio.sleep(_rand_delay(0.2, 0.5))
        except Exception:
            pass

    # Fill last name
    if last_name:
        try:
            ln = page.locator("input[data-cy='duns-lookup-form-last-name']")
            await ln.fill(last_name)
            await asyncio.sleep(_rand_delay(0.2, 0.5))
        except Exception:
            pass

    # Fill email (required)
    try:
        em = page.locator("input[data-cy='duns-lookup-form-email']")
        await em.fill(email_address)
        await asyncio.sleep(_rand_delay(0.3, 0.6))
        logger.info(f"Filled email: {email_address}")
    except Exception as e:
        logger.error(f"Could not fill email field: {e}")
        return {"found": True, "source": "dnb_uk_browser",
                "form_ready": True, "duns_emailed": False,
                "message": f"Form appeared but email fill failed: {e}"}

    # Take pre-submit screenshot
    try:
        ss = os.path.join(os.path.dirname(__file__), "debug_screenshots", "06_form_filled.png")
        os.makedirs(os.path.dirname(ss), exist_ok=True)
        await page.screenshot(path=ss, full_page=True)
    except Exception:
        pass

    # --- Step 3: Submit the form ---
    try:
        submit = page.locator("button[data-cy='duns-lookup-form-submit']")
        await submit.scroll_into_view_if_needed()
        await asyncio.sleep(_rand_delay(0.3, 0.6))
        await submit.click(force=True)
        logger.info("Submitted DUNS email form")
    except Exception:
        # Fallback: any submit button in the form
        try:
            submit = page.locator(f"{form_sel} button[type='submit']")
            await submit.click(force=True)
            logger.info("Submitted via fallback selector")
        except Exception as e:
            return {"found": True, "source": "dnb_uk_browser",
                    "form_ready": True, "duns_emailed": False,
                    "message": f"Form filled but submit failed: {e}"}

    await asyncio.sleep(_rand_delay(3.0, 5.0))

    # Take post-submit screenshot
    try:
        ss = os.path.join(os.path.dirname(__file__), "debug_screenshots", "07_submitted.png")
        await page.screenshot(path=ss, full_page=True)
    except Exception:
        pass

    # Check for success confirmation on the page
    page_text = ""
    try:
        page_text = (await page.inner_text("body"))[:3000].lower()
    except Exception:
        pass

    success_indicators = ["thank", "email", "sent", "check your", "inbox", "success"]
    is_success = any(ind in page_text for ind in success_indicators)

    return {
        "found": True,
        "source": "dnb_uk_browser",
        "form_ready": True,
        "duns_emailed": True,
        "email_used": email_address,
        "confirmed": is_success,
        "message": (
            "DUNS request submitted. D&B will email the DUNS number to "
            f"{email_address}. Check inbox shortly."
        ),
    }


# ---------------------------------------------------------------------------
# FLOW 2: D&B "I'm a Google Developer" application
# ---------------------------------------------------------------------------

DNB_GET_DUNS_URL = "https://www.dnb.com/duns/get-a-duns.html"


async def _dnb_google_dev_apply(context, company_data, email_address):
    """
    Navigate to D&B's 'Get a DUNS' page, select 'I'm a Google Developer',
    and fill in the application form with company data.

    Returns a dict describing the outcome.
    """
    page = await _get_page(context)

    try:
        logger.info(f"Navigating to D&B application page: {DNB_GET_DUNS_URL}")
        if not await _safe_goto(page, DNB_GET_DUNS_URL):
            return {"success": False, "error": "Could not load D&B application page"}
        await asyncio.sleep(_rand_delay(3.0, 5.0))

        # Dismiss cookies
        await _dismiss_cookies(page)
        await asyncio.sleep(_rand_delay(1.0, 2.0))

        # Random scroll + mouse to look human
        await _random_scroll(page, "down", random.randint(200, 400))
        await _random_mouse_movements(page, count=random.randint(3, 5))
        await asyncio.sleep(_rand_delay(1.0, 2.0))

        # Click "I'm a Google Developer"
        google_dev_selectors = [
            "text=I'm a Google Developer",
            "text=Google Developer",
            "button:has-text('Google Developer')",
            "a:has-text('Google Developer')",
            "[class*='option']:has-text('Google')",
        ]
        google_clicked = False
        for sel in google_dev_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await _human_click(page, sel)
                    google_clicked = True
                    logger.info("Selected 'I'm a Google Developer' option")
                    break
            except Exception:
                continue

        if not google_clicked:
            # Try international-based business as fallback
            intl_selectors = [
                "text=International-based business",
                "text=international",
                "button:has-text('International')",
            ]
            for sel in intl_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await _human_click(page, sel)
                        logger.info("Selected 'International-based business' option (fallback)")
                        google_clicked = True
                        break
                except Exception:
                    continue

        if not google_clicked:
            return {"success": False, "error": "Could not find developer option on D&B page"}

        await asyncio.sleep(_rand_delay(2.0, 4.0))
        await _wait_for_load(page, timeout=20000)

        # Now we should be on the form page.  Fill in company information.
        app = company_data.get("duns_application", company_data)

        form_fields = {
            "business_name|company_name|legal_name|businessName|companyName": app.get("business_name", ""),
            "street|address_line_1|streetAddress|address1": app.get("street_address", ""),
            "address_line_2|address2|suite": app.get("street_address_2", ""),
            "city|town|locality": app.get("city", ""),
            "state|province|region|county": app.get("state_province", ""),
            "postal_code|postcode|zip|postalCode": app.get("postal_code", ""),
            "phone|telephone|phoneNumber": app.get("phone", ""),
            "email|emailAddress|contact_email": email_address,
            "ceo|owner|principal|ceoName": app.get("ceo_name", ""),
            "year|yearStarted|established": app.get("year_started", ""),
            "employee|employees|numEmployees": app.get("employees", ""),
        }

        filled_count = 0
        for field_pattern, value in form_fields.items():
            if not value:
                continue
            names = field_pattern.split("|")
            for name in names:
                input_selectors = [
                    f"input[name*='{name}' i]",
                    f"input[id*='{name}' i]",
                    f"input[placeholder*='{name}' i]",
                    f"textarea[name*='{name}' i]",
                ]
                typed = False
                for sel in input_selectors:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=1000):
                            await _human_type(page, sel, str(value))
                            filled_count += 1
                            typed = True
                            break
                    except Exception:
                        continue
                if typed:
                    break

        # Try to select country (GB / United Kingdom)
        country_selectors = [
            "select[name*='country' i]",
            "select[id*='country' i]",
        ]
        for sel in country_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    try:
                        await el.select_option(value="GB")
                    except Exception:
                        try:
                            await el.select_option(label="United Kingdom")
                        except Exception:
                            pass
                    await asyncio.sleep(_rand_delay(0.5, 1.0))
                    break
            except Exception:
                continue

        logger.info(f"Filled {filled_count} form fields")

        # Take a screenshot for verification (saved to temp, not submitted)
        screenshot_path = os.path.join(
            os.path.dirname(__file__),
            f"duns_form_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
        )
        await page.screenshot(path=screenshot_path, full_page=True)

        # Capture the current URL and page state
        current_url = page.url
        page_title = await page.title()

        return {
            "success": True,
            "filled_fields": filled_count,
            "current_url": current_url,
            "page_title": page_title,
            "screenshot": screenshot_path,
            "message": (
                f"Form pre-filled with {filled_count} fields. "
                "Screenshot saved for verification. "
                "Form NOT auto-submitted - review screenshot and submit manually if needed."
            ),
            "note": "Auto-submission disabled for safety. Review the screenshot before proceeding.",
        }

    except Exception as e:
        logger.error(f"D&B application error: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Public API: orchestrated lookup with fallback
# ---------------------------------------------------------------------------

async def _run_duns_lookup_async(company_number, company_name, post_town="",
                                  post_code="", email_address="",
                                  first_name="", last_name="", headless=True):
    """
    Async entry point:
      1. Try D&B UK lookup by registration number
      2. If not found, try D&B UK lookup by company name
    Returns the result dict.
    """
    # D&B blocks headless browsers entirely - force visible mode
    if headless:
        logger.info("Overriding headless=True -> False (D&B blocks headless browsers)")
    async with async_playwright() as pw:
        context = await _create_stealth_context(pw, headless=False)
        try:
            # Pre-browse: visit a neutral site first to warm the profile
            warmup_page = await _get_page(context)
            await _safe_goto(warmup_page, "https://www.google.co.uk")
            await asyncio.sleep(_rand_delay(1.5, 3.0))
            await _random_mouse_movements(warmup_page, count=random.randint(2, 4))

            result = await _dnb_uk_lookup(
                context,
                company_number=company_number,
                company_name=company_name,
                post_town=post_town,
                post_code=post_code,
                email_address=email_address,
                first_name=first_name,
                last_name=last_name,
            )
            return result
        finally:
            await context.close()


async def _run_duns_apply_async(company_data, email_address, headless=True):
    """
    Async entry point for D&B DUNS application.
    """
    async with async_playwright() as pw:
        context = await _create_stealth_context(pw, headless=headless)
        try:
            # Warm up the profile
            warmup_page = await _get_page(context)
            await _safe_goto(warmup_page, "https://www.google.co.uk")
            await asyncio.sleep(_rand_delay(1.5, 3.0))
            await _random_mouse_movements(warmup_page, count=random.randint(2, 4))

            result = await _dnb_google_dev_apply(context, company_data, email_address)
            return result
        finally:
            await context.close()


# ---------------------------------------------------------------------------
# Synchronous wrappers (for Flask integration)
# ---------------------------------------------------------------------------

def _get_or_create_event_loop():
    """Get existing event loop or create a new one safely."""
    try:
        loop = asyncio.get_running_loop()
        # If we're inside an already-running loop, we'll need a new thread
        return None
    except RuntimeError:
        return asyncio.new_event_loop()


def stealth_duns_lookup(company_number, company_name="", post_town="",
                        post_code="", email_address="", first_name="",
                        last_name="", headless=True):
    """
    Synchronous wrapper: look up DUNS via D&B UK.
    
    If email_address is provided, will also fill and submit the form
    so D&B emails the DUNS number to that address.

    Returns:
        dict with keys: found (bool), duns_emailed (bool), email_used (str), ...
    """
    loop = _get_or_create_event_loop()
    if loop is None:
        # Running inside an existing event loop (shouldn't happen in Flask, but safety net)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: asyncio.run(_run_duns_lookup_async(
                    company_number, company_name, post_town, post_code,
                    email_address, first_name, last_name, headless
                ))
            )
            return future.result(timeout=120)
    else:
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _run_duns_lookup_async(
                    company_number, company_name, post_town, post_code,
                    email_address, first_name, last_name, headless
                )
            )
        finally:
            loop.close()


def stealth_duns_apply(company_data, email_address, headless=True):
    """
    Synchronous wrapper: fill in D&B DUNS application form via browser.

    Returns:
        dict with keys: success (bool), filled_fields (int), screenshot (str), ...
    """
    loop = _get_or_create_event_loop()
    if loop is None:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: asyncio.run(_run_duns_apply_async(
                    company_data, email_address, headless
                ))
            )
            return future.result(timeout=120)
    else:
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _run_duns_apply_async(company_data, email_address, headless)
            )
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Full pipeline: lookup then apply if not found
# ---------------------------------------------------------------------------

def stealth_duns_full_pipeline(company_number, company_name="", post_town="",
                                post_code="", company_data=None,
                                email_address="", headless=True):
    """
    Complete stealth pipeline:
      1. Try instant DUNS lookup via D&B UK
      2. If not found and company_data + email provided, pre-fill application form

    Returns combined result dict.
    """
    result = {
        "company_number": company_number,
        "lookup": None,
        "application": None,
        "duns_number": None,
        "status": "not_found",
    }

    # Step 1: Lookup
    lookup = stealth_duns_lookup(
        company_number=company_number,
        company_name=company_name,
        post_town=post_town,
        post_code=post_code,
        headless=headless,
    )
    result["lookup"] = lookup

    if lookup and lookup.get("found"):
        duns = lookup.get("primary_duns")
        if duns:
            result["duns_number"] = duns
            result["status"] = "found"
            return result

    # Step 2: Apply (only pre-fills, does not auto-submit)
    if company_data and email_address:
        application = stealth_duns_apply(
            company_data=company_data,
            email_address=email_address,
            headless=headless,
        )
        result["application"] = application
        if application and application.get("success"):
            result["status"] = "application_prepared"

    return result
