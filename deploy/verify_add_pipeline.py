#!/usr/bin/env python3
"""Exercise the add pipeline on this server. Writes NOTHING.

    cd /opt/company_registration
    .venv/bin/python deploy/verify_add_pipeline.py

Adding a company is the one route that puts a company INTO the pipeline,
and it now also fills its row from Companies House. That makes it worth
proving on the machine that runs it rather than only in a test
environment, because three of the things it depends on are properties of
THIS box and of nowhere else: the deployed commit, whether .env carries a
Companies House key, and whether this server can reach Companies House at
all.

Every database write is intercepted before anything runs, and the
interception is asserted to still be in place at the end. So this calls
the real route, with the real credential, against the real API, and leaves
the production data untouched. It does not log in, because it calls the
blueprint directly - the HTTP auth gate is not what is under test here.

What it checks, in order:

  1. the deployed commit, and that the loaded api_v2 is the deployed file
  2. that .env gives the app a Companies House key, and that a WRONG key
     is refused - without which a success proves only that the endpoint
     answered
  3. that a company with Kenyan directors is admitted and its row filled
  4. that a company with none is refused, with no row created and no
     column written
  5. that nothing escaped the interception

Exit status is 0 only if every check passed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(os.environ.get("APP_DIR") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

# Four live companies, chosen because each one tests something different
# and the first three are admissible for a different reason.
CASES = [
    ("FC009164", "KENYA AIRWAYS PLC - 3 Kenyan directors among 5", True),
    ("SC461938", "a dual national, 'British,Kenyan'", True),
    ("FC010541", "11 Kenyan directors, all appointed the same day", True),
    ("08227886", "British director only - MUST be refused", False),
]

CH_URL = "https://api.company-information.service.gov.uk/search/companies"

passed = 0
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global passed
    if cond:
        passed += 1
        print(f"  [ok] {name}")
    else:
        failed.append(name)
        print(f"  [!!] {name}" + (f"\n         {detail}" if detail else ""))
    return bool(cond)


def say(title: str) -> None:
    print(f"\n=== {title}")


# ── 1. what is actually deployed ────────────────────────────────────────
say("1. deployed code")
try:
    head = subprocess.run(["git", "-C", str(APP_DIR), "log", "--oneline", "-1"],
                          capture_output=True, text=True, timeout=30)
    print(f"    HEAD: {head.stdout.strip() or head.stderr.strip()}")
    dirty = subprocess.run(["git", "-C", str(APP_DIR), "status", "--short"],
                           capture_output=True, text=True, timeout=30)
    check("the working tree matches the commit", not dirty.stdout.strip(),
          f"uncommitted changes on the server:\n{dirty.stdout}")
except Exception as e:                                   # noqa: BLE001
    print(f"    could not read git state: {e}")

from dotenv import load_dotenv                           # noqa: E402

load_dotenv(APP_DIR / ".env")

import db                                                # noqa: E402
import api_v2                                            # noqa: E402

check("the api_v2 in use is the deployed file",
      Path(api_v2.__file__).resolve() == (APP_DIR / "api_v2.py").resolve(),
      f"loaded {api_v2.__file__}")
check("this build has the autofill",
      hasattr(api_v2, "_autofill_company"),
      "the deployed api_v2 predates the autofill - git pull and restart")
check("...and the shared officer parsing",
      "directors" in sys.modules or __import__("importlib").util.find_spec(
          "directors") is not None,
      "directors.py is missing from the deployment")

# ── 2. intercept every write, BEFORE anything can run ───────────────────
say("2. production writes are intercepted")
writes: list[tuple[str, dict]] = []
adds: list[tuple[str, str, str]] = []
real_update, real_add = db.update_row, db.add_company


def _no_update(cn, updates):
    writes.append((cn, dict(updates)))


def _no_add(cn, name="", date_of_creation=""):
    adds.append((cn, name, date_of_creation))
    return True


db.update_row = _no_update
db.add_company = _no_add
db.find_row = lambda cn: None          # so every case takes the add path
check("db.update_row is intercepted", db.update_row is not real_update)
check("db.add_company is intercepted", db.add_company is not real_add)

# ── 3. the credential, and a control that proves the check can fail ─────
say("3. Companies House, from this server")
import requests                                          # noqa: E402

key = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
if not check("the app can see COMPANIES_HOUSE_API_KEY", bool(key),
             "it is not in .env - run: bash deploy/set_ch_key.sh"):
    print("\n    Nothing below can pass without it. Stopping here.")
    raise SystemExit(1)
print(f"    key is {len(key)} chars (never printed)")

params = {"q": "ltd", "items_per_page": 1}
try:
    r = requests.get(CH_URL, params=params, auth=(key, ""), timeout=30)
except Exception as e:                                   # noqa: BLE001
    check("this server can reach Companies House", False,
          f"{type(e).__name__}: {e}")
    raise SystemExit(1)
check("this server can reach Companies House", r.status_code == 200,
      f"HTTP {r.status_code}: {r.text[:200]}")
print(f"    quota remaining this window: "
      f"{r.headers.get('X-Ratelimit-Remain')} of "
      f"{r.headers.get('X-Ratelimit-Limit')}")

# Without this, a 200 above says only that the endpoint answered.
wrong = key[:-1] + ("0" if key[-1] != "0" else "1")
r2 = requests.get(CH_URL, params=params, auth=(wrong, ""), timeout=30)
check("...and a WRONG key is refused, so that success is real",
      r2.status_code != 200,
      f"a wrong key also returned {r2.status_code} - this endpoint is not "
      f"authenticating and the check above means nothing")

# ── 4. the route itself ─────────────────────────────────────────────────
say("4. the add route, on real companies")
from flask import Flask                                  # noqa: E402

app = Flask(__name__)
app.register_blueprint(api_v2.api_v2)
client = app.test_client()

for cn, label, admissible in CASES:
    print(f"\n  -- {cn}  {label}")
    writes.clear()
    adds.clear()
    api_v2._nat_cache.clear()
    api_v2._off_cache.clear()
    resp = client.post("/api/v2/company/add",
                       json={"cn": cn, "name": "NOT THE REGISTERED NAME",
                             "year": "1999"})
    body = resp.get_json() or {}

    if not admissible:
        check(f"{cn} is refused", resp.status_code == 422,
              f"HTTP {resp.status_code}: {body}")
        print(f"     reason: {body.get('reason')}")
        check(f"{cn}: no row was created", adds == [], f"{adds}")
        check(f"{cn}: no column was written", writes == [], f"{writes}")
        continue

    if not check(f"{cn} is added", resp.status_code == 201,
                 f"HTTP {resp.status_code}: {body}"):
        continue
    if body.get("autofill_error"):
        check(f"{cn}: the row was filled", False, body["autofill_error"])
        continue
    filled = writes[0][1] if writes else {}
    for col in sorted(filled):
        print(f"       {col:24s} = {filled[col]!r}")
    check(f"{cn}: the row was filled", len(filled) >= 6,
          f"only {len(filled)} column(s): {sorted(filled)}")
    check(f"{cn}: the registered name replaced the typed one",
          filled.get("Company Name", "") not in ("", "NOT THE REGISTERED NAME"),
          f"got {filled.get('Company Name')!r}")
    check(f"{cn}: a Kenyan director is named",
          "kenyan" in str(filled.get("Director Nationalities", "")).lower(),
          f"got {filled.get('Director Nationalities')!r}")
    check(f"{cn}: no column was written blank",
          all(v for v in filled.values()),
          f"blank: {[k for k, v in filled.items() if not v]}")

# ── 5. nothing escaped ──────────────────────────────────────────────────
say("5. production data")
check("db.update_row was never restored", db.update_row is _no_update)
check("db.add_company was never restored", db.add_company is _no_add)

# Replacing db.update_row only covers callers that reach it through the
# MODULE. A caller that imported the writer by name would hold the
# original and write straight past the interception, so the two checks
# above would pass while production was being written. Measured across
# the deployed tree: no file does that.
#
# The needle is assembled at runtime and this file excludes itself -
# written as a literal, the pattern is a string in THIS file and in the
# comment describing it, so the scan found itself and reported the
# checker as the thing bypassing the check.
needle = "from " + "db" + " import"
me = Path(__file__).resolve()
bound = [str(p.relative_to(APP_DIR)) for p in APP_DIR.rglob("*.py")
         if p.resolve() != me
         and not any(part == ".venv" or part.startswith("venv")
                     for part in p.parts)
         and needle in p.read_text(encoding="utf-8", errors="ignore")]
check("no module holds a direct reference to a db writer", not bound,
      f"these bypass the interception, so the run above may have written "
      f"to production: {bound}")

print()
print(f"{passed} passed / {len(failed)} failed")
for f in failed:
    print(f"  failed: {f}")
if not failed:
    print("\n    The add pipeline works on this server and wrote nothing.")
raise SystemExit(1 if failed else 0)
