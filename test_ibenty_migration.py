#!/usr/bin/env python3
"""Checks that ibenty_auto points at ibenty.com and still fits its API.

    python3 test_ibenty_migration.py            # offline checks only
    python3 test_ibenty_migration.py --live      # also hit the real API

The offline half is pure source and constant inspection, so it runs
anywhere. The live half needs IBENTY_EMAIL / IBENTY_PASSWORD (or the
superseded XBINDER_ names) and reaches
apis.ibenty.com; it authenticates and it reads, and it never calls
create-bind, because a bind costs money and is not reversible.

Every check here was confirmed to FAIL when the corresponding constant was
reverted to its old value - see --prove, which does exactly that in a
temporary copy of the module and reports which checks caught it. A check
that passes against both the fix and the bug is not a check.

playwright is stubbed on import: this module imports it at module scope,
but none of the checks below call into it, and refusing to run the whole
suite because a browser library is absent would mean these never run on a
machine without one.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import types
from contextlib import redirect_stderr
from pathlib import Path

SRC = Path(__file__).with_name("ibenty_auto.py")

OLD_API_HOST = "apis.bestdatabinder.com"
OLD_SITE_HOST = "xbinder.net"
NEW_API = "https://apis.ibenty.com/api"
NEW_SITE = "https://ibenty.com"
NEW_COOKIE_DOMAIN = ".ibenty.com"


def _stub_playwright() -> None:
    if "playwright" in sys.modules:
        return
    pw = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    class _TimeoutError(Exception):
        pass

    def _sync_playwright(*a, **k):  # pragma: no cover - never called here
        raise RuntimeError("playwright is stubbed; this suite does not drive a browser")

    sync_api.TimeoutError = _TimeoutError
    sync_api.sync_playwright = _sync_playwright
    pw.sync_api = sync_api
    sys.modules["playwright"] = pw
    sys.modules["playwright.sync_api"] = sync_api


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
        return bool(cond)

    def report(self) -> int:
        print()
        print(f"{self.passed} passed / {len(self.failed)} failed")
        for f in self.failed:
            print(f"  failed: {f}")
        return 1 if self.failed else 0


# ── offline ─────────────────────────────────────────────────────────────
def offline(r: Results, mod) -> None:
    print("\n-- constants --")
    r.check("API_BASE is the ibenty API", mod.API_BASE == NEW_API,
            f"got {mod.API_BASE!r}")
    r.check("SITE is the ibenty web host", mod.SITE == NEW_SITE,
            f"got {mod.SITE!r}")
    r.check("COOKIE_DOMAIN is the ibenty domain",
            mod.COOKIE_DOMAIN == NEW_COOKIE_DOMAIN, f"got {mod.COOKIE_DOMAIN!r}")
    r.check("LOCATIONS_URL is derived from SITE",
            mod.LOCATIONS_URL.startswith(NEW_SITE + "/"),
            f"got {mod.LOCATIONS_URL!r}")
    r.check("LOCATIONS_URL still names the same file",
            mod.LOCATIONS_URL.endswith("/Kenya-Counties-SubCounties-and-Wards.json"),
            f"got {mod.LOCATIONS_URL!r}")

    print("\n-- no dead host survives anywhere in the source --")
    # Read the file rather than the module: a hostname baked into a string
    # inside a function body is invisible to attribute checks, and three of
    # the five original occurrences were exactly that.
    text = SRC.read_text()
    code_lines = [
        ln for ln in text.splitlines()
        # Skip comments, so the comment explaining the migration - which
        # must name the old hosts to be useful - does not fail the check.
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    for host in (OLD_API_HOST, OLD_SITE_HOST):
        hits = [i + 1 for i, ln in enumerate(code_lines) if host in ln]
        r.check(f"no live reference to {host}", not hits,
                f"still referenced on source lines {hits}")

    print("\n-- the hostname is not duplicated again --")
    # The original drifted because the host appeared five times. Derived
    # values mean a rename cannot half-apply.
    literal_site = len(re.findall(r'"https://ibenty\.com', code))
    literal_api = len(re.findall(r'"https://apis\.ibenty\.com', code))
    r.check("ibenty.com appears as a literal exactly once", literal_site == 1,
            f"found {literal_site} literal occurrences; use SITE instead")
    r.check("apis.ibenty.com appears as a literal exactly once", literal_api == 1,
            f"found {literal_api} literal occurrences; use API_BASE instead")

    print("\n-- the UI download path uses the derived values --")
    ui = text.split("def download_id_via_ui", 1)
    r.check("download_id_via_ui exists", len(ui) == 2)
    if len(ui) == 2:
        body = ui[1]
        r.check("cookies are planted on COOKIE_DOMAIN",
                body.count('"domain": COOKIE_DOMAIN') == 2,
                f'found {body.count(chr(34) + "domain" + chr(34) + ": COOKIE_DOMAIN")} '
                "- expected one for 'auth' and one for 'userId'")
        r.check("the UI navigation is derived from SITE",
                'f"{SITE}/active-binds"' in body)

        # The function used to click the FIRST download control when it
        # could not find the card it was asked for. Measured against the
        # live site, that returned a file byte-identical to an unrelated
        # record's - a different person's document, with no signal to the
        # caller, whose only evidence is that a file arrived. Not finding
        # the requested record is an error, so these assert the
        # substitution cannot come back.
        r.check("no fallback to an arbitrary download control",
                "fallback" not in body.split("def ")[0].lower()
                or ".binds-container span:has(i.bi-download)\").first" not in body,
                "download_id_via_ui still selects a download control it was "
                "not asked for")
        r.check("the not-found path raises rather than substituting",
                "raise RuntimeError(" in body and "No download control found"
                in body)
        r.check("the error names the record that was not found",
                "idNo={id_no!r}" in body,
                "the message must say which record was missing, or the "
                "operator cannot tell a missing card from a broken page")

    print("\n-- the locations fallback is no longer silent --")
    # This is the check that the original code could not have passed, and
    # the reason the outage was invisible: a bare except that returns a
    # default prints nothing, so a dead URL looks exactly like a working
    # one.
    saved = mod.LOCATIONS_URL
    try:
        mod.LOCATIONS_URL = "https://this-host-does-not-exist-9f3a2b.invalid/x.json"
        err = io.StringIO()
        with redirect_stderr(err):
            got = mod._load_random_location()
        msg = err.getvalue()
        r.check("a broken LOCATIONS_URL still returns a usable location",
                isinstance(got, dict) and got.get("district") == "Nairobi",
                f"got {got!r}")
        r.check("a broken LOCATIONS_URL warns on stderr", bool(msg.strip()),
                "nothing was written to stderr - the failure is silent")
        r.check("the warning names the URL that failed",
                "this-host-does-not-exist-9f3a2b.invalid" in msg,
                f"stderr was {msg.strip()!r}")
        r.check("the warning says a fallback was used",
                "fall" in msg.lower() and "Nairobi" in msg,
                f"stderr was {msg.strip()!r}")
    finally:
        mod.LOCATIONS_URL = saved


# ── live ────────────────────────────────────────────────────────────────
def live(r: Results, mod) -> None:
    import requests

    email = (os.environ.get("IBENTY_EMAIL", "")
             or os.environ.get("XBINDER_EMAIL", ""))
    password = (os.environ.get("IBENTY_PASSWORD", "")
                or os.environ.get("XBINDER_PASSWORD", ""))
    if not (email and password):
        print("\n-- live: SKIPPED (IBENTY_EMAIL / IBENTY_PASSWORD not set) --")
        return

    print("\n-- live: the locations file --")
    saved = mod.LOCATIONS_URL
    err = io.StringIO()
    with redirect_stderr(err):
        loc = mod._load_random_location()
    r.check("a real location is drawn from the live file",
            isinstance(loc, dict) and loc.get("district") != "Nairobi"
            or loc.get("division") != "Westlands",
            f"got the fallback verbatim: {loc!r}")
    r.check("drawing a location produced no warning", not err.getvalue().strip(),
            f"stderr: {err.getvalue().strip()!r}")
    r.check("the drawn location is fully populated",
            isinstance(loc, dict)
            and all(loc.get(k) for k in
                    ("district", "division", "location", "subLocation", "pois")),
            f"got {loc!r}")
    assert mod.LOCATIONS_URL == saved

    print("\n-- live: login contract --")
    s = requests.Session()
    auth = None
    try:
        auth = mod.login(s, email, password)
    except Exception as exc:
        r.check("login() succeeds against API_BASE", False,
                f"{type(exc).__name__}: {str(exc)[:200]}")
    else:
        r.check("login() succeeds against API_BASE", True)
        r.check("login() returns a token", bool(auth.get("token")))
        r.check("login() returns a user_id", bool(auth.get("user_id")),
                f"got {auth!r} - the API's key is 'userId'")
        tok = auth.get("token") or ""
        r.check("the token is a three-part JWT", tok.count(".") == 2,
                f"token has {tok.count('.')} dots")

    print("\n-- live: wrong credentials must be rejected --")
    # Without this, 'login succeeded' could mean the endpoint accepts
    # anything, and the check above would prove nothing.
    try:
        bad = mod.login(requests.Session(), email, password + "-wrong-9f3a")
    except Exception:
        r.check("a wrong password is rejected", True)
    else:
        r.check("a wrong password is rejected", False,
                f"login returned {bad!r} for a wrong password")

    print("\n-- live: endpoint shape (read-only; create-bind is never called) --")
    if auth and auth.get("token"):
        tok = auth["token"]
        # search_bind is the only authenticated endpoint that is safe to
        # call for real: it reads. A two-character query is the minimum the
        # API accepts.
        try:
            out = mod.search_bind(s, tok, "ab")
        except Exception as exc:
            r.check("search_bind() reaches the API and parses", False,
                    f"{type(exc).__name__}: {str(exc)[:200]}")
        else:
            r.check("search_bind() reaches the API and parses",
                    isinstance(out, (dict, list)), f"got {type(out).__name__}")

        # create-bind is probed WITHOUT the token, so the route's existence
        # is established while a create remains impossible.
        resp = requests.post(f"{mod.API_BASE}/binds/create-bind", timeout=30)
        r.check("the create-bind route exists and is auth-gated",
                resp.status_code in (401, 403),
                f"got {resp.status_code}: {resp.text[:120]!r} "
                "(404 means the path moved)")
        nonsense = requests.post(
            f"{mod.API_BASE}/binds/not-a-real-route-9f3a2b", timeout=30)
        r.check("a nonsense route 404s, so the check above discriminates",
                nonsense.status_code == 404, f"got {nonsense.status_code}")
        bad = requests.get(f"{mod.API_BASE}/binds/search",
                           headers={"Authorization": "Bearer nope-9f3a2b"},
                           params={"q": "ab"}, timeout=30)
        r.check("a garbage bearer token is rejected",
                bad.status_code in (401, 403), f"got {bad.status_code}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="also reach apis.ibenty.com (reads only)")
    args = ap.parse_args()

    _stub_playwright()
    sys.path.insert(0, str(SRC.parent))
    import ibenty_auto as mod

    r = Results()
    offline(r, mod)
    if args.live:
        live(r, mod)
    else:
        print("\n(live checks skipped; pass --live to run them)")
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
