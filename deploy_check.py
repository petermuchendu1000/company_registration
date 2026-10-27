#!/usr/bin/env python3
"""Deployment self-check. Read-only: it creates and changes nothing.

Run on the server after `git pull`:

    cd /opt/company_registration
    xvfb-run -a .venv/bin/python deploy_check.py

Exists because the alternative is pasting a long script into a terminal,
and a noVNC console silently drops and reorders keystrokes on a large
paste - which produced a SyntaxError that looked like a code fault and was
really a transport fault. A checked-in script is pasted as one short line
and is repeatable on every future deploy.

Each check states what would FALSIFY it rather than just printing a tick.
The database section is skipped, not failed, when DATABASE_URL is unset, so
this is useful before as well as after the database is wired up.

Exit code is the number of failed checks, so `echo $?` is meaningful.
"""
from __future__ import annotations

import asyncio
import importlib.metadata as md
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

RESULTS: list[tuple[str, str, str]] = []
BROWSER_TIMEOUT = 90


def load_env() -> str:
    """Honour .env the way every entry point does (app.py:14, db.py:37).

    Without this the database section reports SKIP on a correctly configured
    box: it reads os.environ before importing db, and db is what would
    otherwise have loaded .env. A check that says "not configured" about a
    configured system is worse than no check.
    """
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return "no .env file"
    try:
        from dotenv import load_dotenv
    except Exception as e:                      # pragma: no cover - defensive
        return f".env present but python-dotenv unusable: {e}"
    load_dotenv(path)
    mode = oct(os.stat(path).st_mode & 0o777)
    return f"loaded {path} (mode {mode})"


def record(name: str, state: str, detail: str = "") -> bool:
    RESULTS.append((name, state, detail))
    mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[state]
    print(f"  [{mark}] {name}" + (f": {detail}" if detail else ""))
    return state != "FAIL"


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


# ── 1. Interpreter ──────────────────────────────────────────────────────────
def check_python() -> None:
    section("1. Interpreter")
    v = sys.version_info
    # Pillow 13.0.0 requires >= 3.11 and Debian 12 ships exactly 3.11, so this
    # has no margin: an older base image breaks the pinned dependency set.
    record("python >= 3.11 (Pillow 13 requires it)",
           "PASS" if v >= (3, 11) else "FAIL",
           f"{v.major}.{v.minor}.{v.micro}")
    # sys.prefix differs from base_prefix inside any virtualenv. Testing for
    # the literal string ".venv" in the path instead would fail for every
    # legitimately named environment, which is a false alarm, not a check.
    # What the pins are actually installed into is verified directly below.
    record("running inside a virtualenv, not system python",
           "PASS" if sys.prefix != sys.base_prefix else "FAIL",
           sys.executable)


# ── 2. Pinned dependencies ──────────────────────────────────────────────────
def check_pins() -> None:
    section("2. Pinned dependencies")
    lock = os.path.join(ROOT, "requirements.lock.txt")
    if not os.path.exists(lock):
        record("requirements.lock.txt present", "FAIL", lock)
        return
    want = {}
    with open(lock, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip()
            if "==" in line:
                n, v = line.split("==")
                want[n.strip().lower().replace("_", "-")] = v.strip()
    if not want:
        record("lock file contains pins", "FAIL", "no '==' lines found")
        return
    bad = []
    for n, v in sorted(want.items()):
        try:
            got = md.version(n)
        except Exception:
            got = None
        if got != v:
            bad.append(f"{n} want={v} got={got}")
    record(f"all {len(want)} pins match the lock file",
           "PASS" if not bad else "FAIL", "; ".join(bad))


# ── 3. The stealth import ───────────────────────────────────────────────────
def check_stealth_import() -> None:
    section("3. playwright-stealth API")
    # browser_stealth.py uses the v2 API: `from playwright_stealth import
    # Stealth`, Stealth(), apply_stealth_async. v1 exported a bare
    # stealth_async function and no Stealth class, so an unpinned install
    # that resolved to v1 raises ImportError right here and takes the whole
    # DUNS stage with it. This is the check that pin exists for.
    try:
        import browser_stealth as bs
    except Exception as e:
        record("browser_stealth imports", "FAIL", f"{type(e).__name__}: {e}")
        return
    record("browser_stealth imports", "PASS")
    ok = type(getattr(bs, "_stealth", None)).__name__ == "Stealth"
    record("stealth object is the v2 Stealth class",
           "PASS" if ok else "FAIL",
           type(getattr(bs, "_stealth", None)).__name__)
    record("apply_stealth_async present",
           "PASS" if hasattr(bs._stealth, "apply_stealth_async") else "FAIL")


# ── 4. Virtual display ──────────────────────────────────────────────────────
def check_display() -> bool:
    section("4. Virtual display")
    disp = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not disp:
        record("DISPLAY is set", "FAIL",
               "run this under: xvfb-run -a .venv/bin/python deploy_check.py")
        return False
    record("DISPLAY is set", "PASS", disp)
    try:
        import browser_stealth as bs
        bs._require_display()
        record("browser_stealth._require_display() is satisfied", "PASS")
    except Exception as e:
        record("browser_stealth._require_display() is satisfied", "FAIL", str(e))
        return False
    return True


# ── 5. Headed Chromium ──────────────────────────────────────────────────────
async def _launch_headed() -> str:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        try:
            page = await browser.new_page()
            await page.set_content("<h1 id=x>ok</h1>")
            text = await page.inner_text("#x")
            if text != "ok":
                raise RuntimeError(f"rendered {text!r}, expected 'ok'")
            return browser.version
        finally:
            await browser.close()


def check_browser(have_display: bool) -> None:
    section("5. Headed Chromium")
    if not have_display:
        record("headed chromium launches", "SKIP", "no display")
        return
    # HEADED, not headless. D&B blocks headless, so a headless-only box
    # passes every other check and still cannot do a DUNS lookup.
    try:
        version = asyncio.run(asyncio.wait_for(_launch_headed(),
                                               timeout=BROWSER_TIMEOUT))
        record("headed chromium launches and renders", "PASS", version)
    except asyncio.TimeoutError:
        record("headed chromium launches and renders", "FAIL",
               f"timed out after {BROWSER_TIMEOUT}s")
    except Exception as e:
        record("headed chromium launches and renders", "FAIL",
               f"{type(e).__name__}: {e}")


# ── 6. Database ─────────────────────────────────────────────────────────────
def check_database() -> None:
    section("6. Database")
    if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
        record("DATABASE_URL is set", "SKIP",
               "not configured yet - everything above is still valid")
        return
    record("DATABASE_URL is set", "PASS")
    try:
        import db
    except Exception as e:
        record("db module imports", "FAIL", f"{type(e).__name__}: {e}")
        return

    try:
        with db._cursor() as cur:
            cur.execute("SELECT current_database() AS d, version() AS v")
            row = cur.fetchone()
        record("connects", "PASS", f"{row['d']}, {row['v'].split(',')[0]}")
    except Exception as e:
        record("connects", "FAIL", f"{type(e).__name__}: {e}")
        return

    try:
        with db._cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (db.TABLE,))
            present = {r["column_name"] for r in cur.fetchall()}
    except Exception as e:
        record(f"table '{db.TABLE}' readable", "FAIL", f"{type(e).__name__}: {e}")
        return

    if not present:
        record(f"table '{db.TABLE}' exists", "FAIL",
               "not created yet - run: .venv/bin/python db.py")
        return
    record(f"table '{db.TABLE}' exists", "PASS", f"{len(present)} columns")

    expected = {c for _, c in db.COLUMNS}
    missing = sorted(expected - present)
    record(f"all {len(expected)} mapped columns present",
           "PASS" if not missing else "FAIL",
           f"missing: {', '.join(missing)}" if missing else "")
    record("updated_at present",
           "PASS" if "updated_at" in present else "FAIL")

    try:
        rows = db.all_rows(use_cache=False)
        record("all_rows() round-trips", "PASS", f"{len(rows)} companies")
    except Exception as e:
        record("all_rows() round-trips", "FAIL", f"{type(e).__name__}: {e}")


def main() -> int:
    print("Deployment self-check - read-only, changes nothing")
    print(f"repo: {ROOT}")
    print(f"env:  {load_env()}")
    check_python()
    check_pins()
    check_stealth_import()
    have_display = check_display()
    check_browser(have_display)
    check_database()

    failed = [n for n, s, _ in RESULTS if s == "FAIL"]
    skipped = [n for n, s, _ in RESULTS if s == "SKIP"]
    print()
    print(f"RESULT: {len(RESULTS) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped")
    if skipped:
        print(f"  skipped: {', '.join(skipped)}")
    if failed:
        print(f"  FAILED:  {', '.join(failed)}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
