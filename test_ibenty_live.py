#!/usr/bin/env python3
"""Stage 4 against the real ibenty account, with screenshots.

    python3 test_ibenty_live.py                 # skips without credentials
    python3 test_ibenty_live.py --out DIR       # keep the evidence in DIR
    python3 test_ibenty_live.py --no-headless   # watch it

This is the only thing that exercises stage 4's real functions — login,
search_bind, download_id_via_ui, split_combined_id_image — against the
service they talk to. It calls them rather than reimplementing them,
because a reimplementation would prove ibenty works, not that this pipeline
works against ibenty.

It is deliberately NOT in run_all_suites.py: it needs credentials, drives a
browser, and reaches a third-party site. The rest of the suites are
hermetic and must stay runnable anywhere.

MONEY SAFETY. create_bind is never called. The account holds binds that are
already paid for, so every other path runs against one for nothing; creating
a bind spends real balance and cannot be undone. That is not left to the
reading - create_bind is replaced with a raiser for the whole run and the
substitution is checked at the end, so the invariant is enforced rather than
intended.

PRIVACY. Every bind is a real person. ID numbers and names are used to drive
the locators but never printed: output shows a short digest instead, so a
terminal log, a CI transcript or journald does not become a copy of the
identity data. The downloaded images go to a temporary directory unless
--out says otherwise, because pipeline_output/ is only partly gitignored
and an ID scan is not something to leave in a working tree by accident.

Two things make the download check able to fail, which it otherwise would
not be:

  * The target is deliberately NOT the first card. download_id_via_ui used
    to fall back to clicking the first download control when it could not
    find the card it was asked for, so a test that targets card 0 passes
    whether the primary locator works or not.
  * The download is compared against a download of card 0. "A JPEG arrived"
    is not evidence that the RIGHT JPEG arrived.

The last check is the regression guard for that fallback. It was removed in
aaf5ee2 after a measurement showed the function returning a file
byte-identical to a different person's; asking for a record that is not on
the page must now raise rather than substitute.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

# The container here ships chromium build 1243 while this venv's playwright
# wants 1248. Point launch() at the binary that is actually present, but
# only when it is - the server has its own matching install under
# /opt/ms-playwright (deploy/company-registration.service) and must use it.
_LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
if _LOCAL_CHROMIUM.exists():
    from playwright.sync_api import BrowserType

    _orig_launch = BrowserType.launch

    def _patched_launch(self, **kw):
        kw.setdefault("executable_path", str(_LOCAL_CHROMIUM))
        if os.geteuid() == 0:
            kw.setdefault("args", ["--no-sandbox"])
        return _orig_launch(self, **kw)

    BrowserType.launch = _patched_launch

import requests  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import ibenty_auto as ib  # noqa: E402


def credentials() -> tuple[str, str]:
    """Same preference order as pipeline._ibenty_credentials.

    Resolved here rather than imported, so this file does not drag in db
    and psycopg2 to read two environment variables.
    """
    email = os.environ.get("IBENTY_EMAIL", "").strip()
    password = os.environ.get("IBENTY_PASSWORD", "").strip()
    if email and password:
        return email, password
    return (os.environ.get("XBINDER_EMAIL", "").strip(),
            os.environ.get("XBINDER_PASSWORD", "").strip())


def d(value: object) -> str:
    """A stable short digest, for identifying a record without printing it."""
    s = str(value or "")
    return "#" + hashlib.sha256(s.encode()).hexdigest()[:8] if s else "#none"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


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


def cookies(token: str, user_id) -> list[dict]:
    c = [{"name": "auth", "value": token, "domain": ib.COOKIE_DOMAIN,
          "path": "/", "httpOnly": False, "secure": True, "sameSite": "Lax"}]
    if user_id:
        c.append({"name": "userId", "value": str(user_id),
                  "domain": ib.COOKIE_DOMAIN, "path": "/", "httpOnly": False,
                  "secure": True, "sameSite": "Lax"})
    return c


def as_png(src: Path, dst: Path) -> None:
    from PIL import Image
    Image.open(src).convert("RGB").save(dst, "PNG")


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 4 against the live ibenty account")
    ap.add_argument("--out", help="where to keep screenshots and downloads "
                                  "(default: a fresh temporary directory)")
    ap.add_argument("--no-headless", action="store_true",
                    help="show the browser window")
    args = ap.parse_args()

    email, password = credentials()
    if not (email and password):
        print("-- SKIPPED: set IBENTY_EMAIL and IBENTY_PASSWORD to run this --")
        print("   (XBINDER_EMAIL / XBINDER_PASSWORD are still read)")
        print()
        print("0 passed / 0 failed")
        return 0

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="ibenty_live_"))
    out.mkdir(parents=True, exist_ok=True)
    headless = not args.no_headless

    # MONEY SAFETY, enforced rather than documented. Anything that reaches
    # create_bind now fails loudly instead of spending balance.
    spent: list[str] = []

    def _refuse_to_spend(*a, **k):
        spent.append("create_bind")
        raise AssertionError(
            "create_bind was called. This suite must not buy a bind: the "
            "account already holds paid-for binds and a purchase cannot be "
            "undone.")

    ib.create_bind = _refuse_to_spend

    r = Results()
    print(f"target: {ib.SITE} / {ib.API_BASE}")
    print(f"evidence: {out}")

    VP = {"width": 1440, "height": 1000}
    sess = requests.Session()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        # ── the gate ────────────────────────────────────────────────────
        print("\n-- an unauthenticated visit must be refused --")
        ctx = browser.new_context(viewport=VP)
        pg = ctx.new_page()
        pg.goto(f"{ib.SITE}/active-binds", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2500)
        landed, n0 = pg.url, pg.locator(".binds-container").count()
        pg.screenshot(path=str(out / "01-unauthenticated.png"), full_page=True)
        r.check("no credentials redirects to the login page", "/auth/login" in landed,
                f"landed on {landed}")
        r.check("...and renders no bind cards", n0 == 0, f"{n0} cards")
        ctx.close()

        # ── login ───────────────────────────────────────────────────────
        print("\n-- login() --")
        auth = ib.login(sess, email, password)
        token, user_id = auth["token"], auth.get("user_id")
        r.check("login returns a token", bool(token), f"got {auth.keys()}")
        r.check("...and a userId", user_id is not None, f"user_id={user_id!r}")
        # Without this, "login worked" only means the endpoint answered.
        try:
            ib.login(requests.Session(), email, password + "-wrong-9f3a")
            refused = False
        except Exception:
            refused = True
        r.check("a wrong password is refused", refused,
                "an endpoint that accepts anything makes the success above "
                "meaningless")

        # ── the authenticated list ──────────────────────────────────────
        print("\n-- the token renders this account's binds --")
        ctx = browser.new_context(viewport=VP)
        ctx.add_cookies(cookies(token, user_id))
        pg = ctx.new_page()
        pg.goto(f"{ib.SITE}/active-binds", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(4000)
        cards = pg.locator(".binds-container")
        n = cards.count()
        n_dl = pg.locator(".binds-container span:has(i.bi-download)").count()
        rows = [{"i": i,
                 "idNo": cards.nth(i).locator(".id-number").inner_text().strip(),
                 "name": cards.nth(i).locator(".full-name").inner_text().strip()}
                for i in range(n)]
        pg.screenshot(path=str(out / "02-authenticated.png"), full_page=True)
        for row in rows:
            print(f"   card[{row['i']}] id={d(row['idNo'])} name={d(row['name'])}")
        r.check("the real token renders bind cards", n > 0, f"{n} cards")
        r.check("every card has the download control the code looks for",
                n_dl == n, f"{n} cards, {n_dl} controls")
        ctx.close()

        if n < 2:
            print("\n  Cannot continue: this check needs at least two binds so "
                  "the target is not the first card.")
            browser.close()
            return r.report()

        # NOT card 0, so the old fallback would return a visibly different
        # person and the comparison below can actually fail.
        ti = 5 if n > 5 else n - 1
        target, first = rows[ti], rows[0]
        print(f"\n   target  = card[{ti}] id={d(target['idNo'])}")
        print(f"   card[0] = id={d(first['idNo'])}   <- what a fallback would give")

        # ── search_bind ─────────────────────────────────────────────────
        print("\n-- search_bind() resolves a card to a bind id --")
        res = ib.search_bind(sess, token, target["idNo"]).get("results") or []
        hit = next((x for x in res if str(x.get("idNo")) == target["idNo"]), None)
        bind_id = (hit or {}).get("id", "")
        r.check("the card's ID number resolves to exactly one bind", bool(bind_id),
                f"{len(res)} results, none matching {d(target['idNo'])}")
        nonsense = ib.search_bind(sess, token, "zzzz9f3a2b")
        r.check("a nonsense query returns nothing", nonsense.get("count") == 0,
                f"count={nonsense.get('count')} - if a miss does not look like "
                f"a miss, the hit above proves nothing")

        # ── the primary locator ─────────────────────────────────────────
        print("\n-- the locator download_id_via_ui uses, on the target card --")
        ctx = browser.new_context(viewport=VP)
        ctx.add_cookies(cookies(token, user_id))
        pg = ctx.new_page()
        pg.goto(f"{ib.SITE}/active-binds", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(4000)
        card = pg.locator(f".binds-container:has-text('{target['idNo']}')").first
        card_n = card.count()
        btn_n = 0
        if card_n:
            btn = card.locator("span:has(i.bi-download)").first
            btn_n = btn.count()
            if btn_n:
                btn.evaluate("e=>{e.style.outline='4px solid #ff3b30';"
                             "e.style.outlineOffset='3px';"
                             "e.scrollIntoView({block:'center'})}")
                pg.wait_for_timeout(600)
        pg.screenshot(path=str(out / "03-download-control.png"), full_page=True)
        r.check("the target card is found by its ID number", card_n == 1,
                f"matched {card_n}")
        r.check("...and its download control is found", btn_n == 1,
                f"matched {btn_n} - this is the primary path, not a fallback")
        ctx.close()

        # ── control: a wrong token ──────────────────────────────────────
        print("\n-- a well-formed but wrong token must show nothing --")
        parts = token.split(".")
        bad = ".".join([parts[0], parts[1][:-6] + "AAAAAA", parts[2]])
        ctx = browser.new_context(viewport=VP)
        ctx.add_cookies(cookies(bad, user_id))
        pg = ctx.new_page()
        pg.goto(f"{ib.SITE}/active-binds", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(4000)
        bad_n = pg.locator(".binds-container").count()
        pg.screenshot(path=str(out / "04-control-wrong-token.png"), full_page=True)
        r.check("a wrong token renders no binds", bad_n == 0,
                f"{bad_n} cards. Without this, the list check only proves the "
                f"page loads.")
        ctx.close()
        browser.close()

    if not bind_id:
        print("\n  Cannot continue without a bind id.")
        return r.report()

    # ── the real download ───────────────────────────────────────────────
    print("\n-- download_id_via_ui(), and proof it fetched the RIGHT record --")
    tgt = out / "target.jpeg"
    ib.download_id_via_ui(token, user_id, bind_id, target["idNo"], tgt,
                          headless=headless)
    r.check("a file came back for the target card", tgt.stat().st_size > 1000,
            f"{tgt.stat().st_size} B")

    first_res = ib.search_bind(sess, token, first["idNo"]).get("results") or []
    first_hit = next((x for x in first_res
                      if str(x.get("idNo")) == first["idNo"]), {})
    c0 = out / "card0.jpeg"
    ib.download_id_via_ui(token, user_id, first_hit.get("id", ""),
                          first["idNo"], c0, headless=headless)
    print(f"   target  {tgt.stat().st_size} B sha={sha(tgt)}")
    print(f"   card[0] {c0.stat().st_size} B sha={sha(c0)}")
    r.check("it is a DIFFERENT file from card 0's download", sha(tgt) != sha(c0),
            "identical files mean the click was not steered by the ID number, "
            "so the download check would pass for the wrong reason")
    as_png(tgt, out / "05-downloaded-target.png")
    as_png(c0, out / "06-downloaded-card0.png")

    # ── the split ───────────────────────────────────────────────────────
    print("\n-- split_combined_id_image() --")
    front, back = ib.split_combined_id_image(tgt)
    r.check("the front is written", front.stat().st_size > 1000,
            f"{front.stat().st_size} B - this is what the pipeline stores as "
            f"'ID Front.jpeg'")
    r.check("the back is written", back.stat().st_size > 1000,
            f"{back.stat().st_size} B")
    r.check("front and back are different images", sha(front) != sha(back),
            "a split that returned the same half twice would pass a size check")
    as_png(front, out / "07-split-front.png")
    as_png(back, out / "08-split-back.png")

    # ── the regression guard ────────────────────────────────────────────
    print("\n-- a record that is not on the page must RAISE, not substitute --")
    # The fallback removed in aaf5ee2 clicked the first download control
    # when the requested card could not be found, and returned a file
    # byte-identical to a different person's. The caller cannot detect that:
    # the only signal it gets is "a file arrived".
    absent = out / "must-not-exist.jpeg"
    ABSENT_IDNO = "00000000000000"          # valid shape, not on any card
    err = None
    try:
        ib.download_id_via_ui(token, user_id, "not-a-real-bind-id",
                              ABSENT_IDNO, absent, headless=headless)
    except Exception as e:                  # noqa: BLE001 - this is the check
        err = e
    r.check("asking for an absent record raises", isinstance(err, RuntimeError),
            f"got {err!r}")
    r.check("...naming the record it could not find",
            err is not None and ABSENT_IDNO in str(err), f"message: {err}")
    r.check("...and writes no file in its place", not absent.exists(),
            "a file here would be another person's ID returned silently")

    # ── the money-safety invariant ──────────────────────────────────────
    print("\n-- nothing in this run was allowed to spend --")
    r.check("create_bind was never called", not spent,
            f"called {len(spent)} time(s) - this suite must not buy a bind")
    r.check("...and the guard was really in place",
            ib.create_bind is _refuse_to_spend,
            "if the substitution was undone, the check above is vacuous")

    print(f"\n  evidence in {out}")
    return r.report()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
