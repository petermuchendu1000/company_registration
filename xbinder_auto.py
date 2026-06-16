import argparse
import calendar
import json
import mimetypes
import os
import random
import re
import string
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


API_BASE = "https://apis.bestdatabinder.com/api"
LOCATIONS_URL = "https://xbinder.net/Kenya-Counties-SubCounties-and-Wards.json"
CH_BASE = "https://api.company-information.service.gov.uk"

load_dotenv()
CH_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")


def _pick(data: dict, keys: list[str], default: str = "") -> str:
    for k in keys:
        v = data.get(k)
        if v is None:
            continue
        text = str(v).strip()
        if text:
            return text
    return default


def _to_ddmmyyyy(value: str) -> str:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            d = datetime.strptime(value, fmt)
            return d.strftime("%d.%m.%Y")
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {value}")


def _random_digits(n: int) -> str:
    return "".join(random.choice(string.digits) for _ in range(n))


def _random_date_str(start: date, end: date) -> str:
    delta_days = (end - start).days
    d = start + timedelta(days=random.randint(0, delta_days))
    return d.strftime("%d.%m.%Y")


def _dob_to_ddmmyyyy_random_day(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    # Full-date inputs.
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            d = datetime.strptime(value, fmt)
            return d.strftime("%d.%m.%Y")
        except ValueError:
            pass

    # Year-month input from CH officers API. Pick a valid random day in that month.
    ym = re.fullmatch(r"(\d{4})[-/](\d{1,2})", value)
    if ym:
        year = int(ym.group(1))
        month = int(ym.group(2))
        if month < 1 or month > 12:
            raise ValueError(f"Unsupported DOB month in value: {value}")
        max_day = calendar.monthrange(year, month)[1]
        day = random.randint(1, max_day)
        return f"{day:02d}.{month:02d}.{year:04d}"

    # Year-only input. Pick random month/day with month-aware day bounds.
    y = re.fullmatch(r"(\d{4})", value)
    if y:
        year = int(y.group(1))
        month = random.randint(1, 12)
        max_day = calendar.monthrange(year, month)[1]
        day = random.randint(1, max_day)
        return f"{day:02d}.{month:02d}.{year:04d}"

    raise ValueError(f"Unsupported date format: {value}")


def _load_random_location() -> dict:
    fallback = {
        "district": "Nairobi",
        "division": "Westlands",
        "location": "Parklands/Highridge",
        "subLocation": "Parklands/Highridge",
        "pois": "Westlands",
    }
    try:
        resp = requests.get(LOCATIONS_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        county = random.choice(list(data.keys()))
        subcounty_map = data[county]
        subcounty = random.choice(list(subcounty_map.keys()))
        wards = subcounty_map[subcounty]
        ward = random.choice(wards)
        return {
            "district": county,
            "division": subcounty,
            "location": ward,
            "subLocation": ward,
            "pois": subcounty,
        }
    except Exception:
        return fallback


def _norm_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _match_director_name(ch_name: str, first: str, last: str) -> bool:
    n = _norm_name(ch_name)
    return bool(first and last and _norm_name(first) in n and _norm_name(last) in n)


def _fetch_dob_from_ch_api(company_number: str, first_name: str, last_name: str) -> str:
    if not (CH_API_KEY and company_number):
        return ""
    try:
        resp = requests.get(
            f"{CH_BASE}/company/{company_number}/officers",
            auth=(CH_API_KEY, ""),
            timeout=20,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        items = data.get("items", [])

        active_directors = [
            o
            for o in items
            if not o.get("resigned_on") and "director" in (o.get("officer_role", "") or "").lower()
        ]

        # Prefer matching the parsed director name.
        selected = None
        for o in active_directors:
            if _match_director_name(o.get("name", ""), first_name, last_name):
                selected = o
                break
        if selected is None and active_directors:
            selected = active_directors[0]
        if selected is None:
            return ""

        dob = selected.get("date_of_birth") or {}
        y = dob.get("year")
        m = dob.get("month")
        d = dob.get("day")
        if y and m and d:
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        if y and m:
            return f"{int(y):04d}-{int(m):02d}"
        if y:
            return f"{int(y):04d}"
        return ""
    except Exception:
        return ""


def _parse_ch_director_name(raw_name: str) -> tuple[str, str, str]:
    name = re.sub(r"\s+", " ", raw_name.strip())
    if not name:
        return "", "", ""
    suffixes = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "jr", "sr"}

    if "," in name:
        surname, rest = name.split(",", 1)
        surname = surname.strip()
        tokens = [t.strip(" ,.") for t in rest.split() if t.strip(" ,.")]
        tokens = [t for t in tokens if t.lower().strip(".") not in suffixes]
        first = tokens[0] if tokens else ""
        middle = " ".join(tokens[1:]) if len(tokens) > 1 else ""
        return first.upper(), surname.upper(), middle.upper()

    tokens = [t.strip(" ,.") for t in name.split() if t.strip(" ,.")]
    tokens = [t for t in tokens if t.lower().strip(".") not in suffixes]
    if not tokens:
        return "", "", ""
    first = tokens[0]
    last = tokens[-1] if len(tokens) > 1 else ""
    middle = " ".join(tokens[1:-1]) if len(tokens) > 2 else ""
    return first.upper(), last.upper(), middle.upper()


def load_director_profile(profile_path: Path, company_number: str | None) -> dict:
    if not profile_path.exists():
        raise RuntimeError(f"Director profile file not found: {profile_path}")

    root = json.loads(profile_path.read_text(encoding="utf-8"))

    if company_number:
        if not isinstance(root, dict) or company_number not in root:
            raise RuntimeError(f"Company {company_number} not found in {profile_path}")
        entry = root[company_number]
        selected_company_number = company_number
    else:
        entry = root
        selected_company_number = _pick(entry, ["company_number"])

    # Supports either a flat explicit profile or DUNS request shape with optional xbinder block.
    explicit = entry.get("xbinder_profile") or entry.get("xbinder") or entry
    company_data = entry.get("company_data") or {}
    primary_director = company_data.get("primary_director") or {}

    fname = _pick(explicit, ["fname", "first_name", "firstName"])
    lname = _pick(explicit, ["lname", "last_name", "lastName"])
    sname = _pick(explicit, ["sname", "middle_name", "middleName", "other_name", "otherName"])
    if not (fname and lname):
        parsed_first, parsed_last, parsed_middle = _parse_ch_director_name(_pick(primary_director, ["name"]))
        fname = fname or parsed_first
        lname = lname or parsed_last
        sname = sname or parsed_middle

    raw_dob = _pick(explicit, ["actualdob", "dob", "date_of_birth", "dateOfBirth"])
    if not raw_dob:
        raw_dob = _pick(primary_director, ["date_of_birth", "dob", "dateOfBirth"])
    if not raw_dob and selected_company_number:
        raw_dob = _fetch_dob_from_ch_api(selected_company_number, fname, lname)
    raw_doi = _pick(explicit, ["actualdois", "doi", "date_of_issue", "dateOfIssue"])

    profile = {
        "serialnumber": _pick(explicit, ["serialnumber", "serial_no", "serialNumber"]),
        "idnumber": _pick(explicit, ["idnumber", "id_no", "idNumber"]),
        "fname": fname,
        "lname": lname,
        "sname": sname,
        "gender": _pick(explicit, ["gender", "sex"]).upper(),
        "actualdob": _dob_to_ddmmyyyy_random_day(raw_dob) if raw_dob else "",
        "actualdois": _to_ddmmyyyy(raw_doi) if raw_doi else "",
        "actualdoex": _pick(explicit, ["actualdoex", "doe", "date_of_expiry", "dateOfExpiry"]),
        "cardnumber": _pick(explicit, ["cardnumber", "card_no", "cardNumber"]),
        "district": _pick(explicit, ["district", "county"]),
        "division": _pick(explicit, ["division", "subcounty", "sub_county"]),
        "location": _pick(explicit, ["location", "ward"]),
        "subLocation": _pick(explicit, ["subLocation", "sub_location", "sublocation", "ward"]),
        "pois": _pick(explicit, ["pois", "poi", "division", "subcounty"]),
        "cardType": _pick(explicit, ["cardType", "card_type"], "normal"),
        "passport_image": _pick(explicit, ["passport_image", "passportImage", "passport_path", "passportPath"]),
    }

    # Keep names from source records; auto-fill only non-name details when missing.
    if not profile["serialnumber"]:
        profile["serialnumber"] = _random_digits(9)
    if not profile["idnumber"]:
        profile["idnumber"] = _random_digits(8)
    if profile["gender"] not in {"M", "F"}:
        profile["gender"] = random.choice(["M", "F"])
    if not profile["actualdob"]:
        profile["actualdob"] = _random_date_str(date(1950, 1, 1), date(2004, 12, 31))
    if not profile["actualdois"]:
        profile["actualdois"] = _random_date_str(date(2009, 1, 1), date(2021, 12, 31))
    if not profile["cardnumber"]:
        profile["cardnumber"] = f"T{_random_digits(10)}"

    if not (profile["district"] and profile["division"] and profile["location"] and profile["subLocation"]):
        loc = _load_random_location()
        profile["district"] = profile["district"] or loc["district"]
        profile["division"] = profile["division"] or loc["division"]
        profile["location"] = profile["location"] or loc["location"]
        profile["subLocation"] = profile["subLocation"] or loc["subLocation"]
        profile["pois"] = profile["pois"] or loc["pois"]
    elif not profile["pois"]:
        profile["pois"] = profile["division"]

    required = ["fname", "lname"]
    missing = [k for k in required if not str(profile.get(k, "")).strip()]
    if missing:
        raise RuntimeError(
            "Director profile is missing required fields: "
            + ", ".join(missing)
            + ". Add names under xbinder_profile (or ensure primary_director.name exists)."
        )

    return profile


def build_mrz(serial_no: str, id_no: str, dob_ddmmyyyy: str, doi_ddmmyyyy: str, sex: str, fname: str, lname: str, sname: str) -> tuple[str, str, str]:
    dob = dob_ddmmyyyy[2:].replace(".", "")
    doi = doi_ddmmyyyy[2:].replace(".", "")
    full_surname = f"{fname}<{sname}"
    line_one = f"IDKYA{serial_no}1<<4507<<<<<4507"
    line_two = f"{dob}{sex}{doi}<B{id_no}Y<<8"
    line_three = f"{full_surname}<{lname}<<<<<<<<<<<<<<<<<<<<"
    return line_one[:36], line_two[:36], line_three[:36]


def login(session: requests.Session, email: str, password: str) -> dict:
    resp = session.post(
        f"{API_BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token")
    user_id = body.get("userId")
    if not token:
        raise RuntimeError("Login succeeded but no token returned")
    return {"token": token, "user_id": user_id}


def create_bind(session: requests.Session, token: str, director: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    serial_no = director["serialnumber"]
    id_no = director["idnumber"]
    fname = director["fname"]
    lname = director["lname"]
    sname = director.get("sname", "")
    sex = director["gender"]
    dob = director["actualdob"]
    doi = director["actualdois"]
    card_no = director["cardnumber"]

    mrz1, mrz2, mrz3 = build_mrz(serial_no, id_no, dob, doi, sex, fname, lname, sname)

    data = {
        "serialnumber": serial_no,
        "idnumber": id_no,
        "fname": fname,
        "lname": lname,
        "sname": sname,
        "gender": sex,
        "actualdob": dob,
        "actualdois": doi,
        "actualdoex": "",
        "cardnumber": card_no,
        "mrzlineone": mrz1,
        "mrzlinetwo": mrz2,
        "mrzlinethree": mrz3,
        "district": director["district"],
        "division": director["division"],
        "location": director["location"],
        "subLocation": director["subLocation"],
        "pois": director.get("pois") or director["division"],
        "cardType": director.get("cardType", "normal"),
    }

    passport_path = director.get("passport_image", "")
    if passport_path:
        p = Path(passport_path)
        if not p.exists():
            raise RuntimeError(f"passport_image file does not exist: {p}")
        mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        files = {"passportImage": (p.name, p.read_bytes(), mime)}
    else:
        files = {"passportImage": ("", b"", "application/octet-stream")}
    resp = session.post(
        f"{API_BASE}/binds/create-bind",
        headers=headers,
        data=data,
        files=files,
        timeout=60,
    )
    resp.raise_for_status()
    out = resp.json()
    bind = out.get("bind") or {}

    return {
        "request": data,
        "response": out,
        "bind": bind,
    }


def search_bind(session: requests.Session, token: str, query: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = session.get(
        f"{API_BASE}/binds/search",
        headers=headers,
        params={"q": query, "limit": 10},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def download_file(session: requests.Session, url: str, out_path: Path) -> None:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)


def download_id_via_ui(token: str, user_id: str | None, bind_id: str, id_no: str, out_path: Path, headless: bool = True) -> Path:
    if not bind_id:
        raise RuntimeError("Cannot download ID via UI without bind_id")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        cookies = [
            {
                "name": "auth",
                "value": token,
                "domain": ".xbinder.net",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
        if user_id:
            cookies.append(
                {
                    "name": "userId",
                    "value": str(user_id),
                    "domain": ".xbinder.net",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
        context.add_cookies(cookies)

        page = context.new_page()
        page.goto("https://xbinder.net/active-binds", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        click_target = None
        # Prefer the exact bind card created in this run.
        for _ in range(20):
            if id_no:
                card = page.locator(f".binds-container:has-text('{id_no}')").first
                if card.count() > 0:
                    btn = card.locator("span:has(i.bi-download)").first
                    if btn.count() > 0:
                        click_target = btn
                        break
            page.wait_for_timeout(1000)

        # Fallback to first available download button in the visible list.
        if click_target is None:
            fallback = page.locator(".binds-container span:has(i.bi-download)").first
            if fallback.count() > 0:
                click_target = fallback

        if click_target is None:
            page.screenshot(path=str(out_path.with_suffix(".debug.png")), full_page=True)
            browser.close()
            raise RuntimeError("Could not find ID download control in UI (debug screenshot saved)")

        try:
            with page.expect_download(timeout=60000) as dl:
                click_target.click()
            download = dl.value
            tmp = out_path.with_suffix(".tmp")
            download.save_as(str(tmp))
            data = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            out_path.write_bytes(data)
        except PlaywrightTimeoutError as exc:
            page.screenshot(path=str(out_path.with_suffix(".debug.png")), full_page=True)
            browser.close()
            raise RuntimeError("UI download did not start in time (debug screenshot saved)") from exc

        browser.close()
        return out_path


def _smooth(values: list[float], radius: int) -> list[float]:
    out: list[float] = []
    n = len(values)
    for i in range(n):
        a = max(0, i - radius)
        b = min(n, i + radius + 1)
        out.append(sum(values[a:b]) / (b - a))
    return out


def _segments_above(values: list[float], threshold: float) -> list[tuple[int, int]]:
    idx = [i for i, v in enumerate(values) if v >= threshold]
    if not idx:
        return []

    segs: list[tuple[int, int]] = []
    start = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
            continue
        segs.append((start, prev))
        start = i
        prev = i
    segs.append((start, prev))
    return segs


def split_combined_id_image(combined_path: Path) -> tuple[Path, Path]:
    img = Image.open(combined_path).convert("L")
    width, height = img.size
    px = img.load()

    row_means: list[float] = []
    for y in range(height):
        row_sum = 0
        for x in range(width):
            row_sum += px[x, y]
        row_means.append(row_sum / width)

    col_means: list[float] = []
    for x in range(width):
        col_sum = 0
        for y in range(height):
            col_sum += px[x, y]
        col_means.append(col_sum / height)

    row_smoothed = _smooth(row_means, max(10, height // 50))
    col_smoothed = _smooth(col_means, max(10, width // 70))

    row_thr = min(row_smoothed) + 0.6 * (max(row_smoothed) - min(row_smoothed))
    col_thr = min(col_smoothed) + 0.6 * (max(col_smoothed) - min(col_smoothed))

    y_segments = _segments_above(row_smoothed, row_thr)
    x_segments = _segments_above(col_smoothed, col_thr)

    min_card_h = max(120, height // 8)
    y_candidates = [s for s in y_segments if (s[1] - s[0] + 1) >= min_card_h]
    if len(y_candidates) < 2:
        raise RuntimeError("Could not detect two card regions in combined ID image")

    y_candidates.sort(key=lambda s: (s[1] - s[0] + 1), reverse=True)
    y_top, y_bottom = sorted(y_candidates[:2], key=lambda s: s[0])

    if not x_segments:
        raise RuntimeError("Could not detect horizontal card bounds in combined ID image")

    x_left, x_right = max(x_segments, key=lambda s: s[1] - s[0])

    pad_x = max(4, width // 400)
    pad_y = max(4, height // 300)

    x1 = max(0, x_left - pad_x)
    x2 = min(width, x_right + pad_x + 1)

    y1_top = max(0, y_top[0] - pad_y)
    y2_top = min(height, y_top[1] + pad_y + 1)
    y1_bottom = max(0, y_bottom[0] - pad_y)
    y2_bottom = min(height, y_bottom[1] + pad_y + 1)

    source = Image.open(combined_path).convert("RGB")
    front = source.crop((x1, y1_top, x2, y2_top))
    back = source.crop((x1, y1_bottom, x2, y2_bottom))

    front_path = combined_path.with_name("ID Front.jpeg")
    back_path = combined_path.with_name("ID Back.jpeg")
    front.save(front_path, format="JPEG", quality=95)
    back.save(back_path, format="JPEG", quality=95)

    return front_path, back_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Automate XBINDER flow end-to-end")
    parser.add_argument("--email", required=True, help="XBINDER login email")
    parser.add_argument("--password", required=True, help="XBINDER login password")
    parser.add_argument("--output-dir", default="downloads/xbinder", help="Where to save downloaded files")
    parser.add_argument("--director-json", default="duns_requests.json", help="Path to director details JSON")
    parser.add_argument("--company-number", help="Company number key in duns_requests-style JSON")
    parser.add_argument("--no-split", action="store_true", help="Do not split combined front/back image")
    args = parser.parse_args()

    director = load_director_profile(Path(args.director_json), args.company_number)

    session = requests.Session()
    auth = login(session, args.email, args.password)
    token = auth["token"]
    user_id = auth.get("user_id")
    print("[OK] Logged in")

    created = create_bind(session, token, director)
    bind = created["bind"]
    bind_id = bind.get("id", "")
    id_no = bind.get("idNo", created["request"]["idnumber"])

    print("[OK] Created bind")
    print(f"  bind_id: {bind_id}")
    print(f"  id_no: {id_no}")
    print(f"  county/division/ward: {created['request']['district']} / {created['request']['division']} / {created['request']['location']}")
    print(f"  name: {created['request']['fname']} {created['request']['sname']} {created['request']['lname']}")
    print(f"  dob(actualdob): {created['request']['actualdob']}")

    search = search_bind(session, token, id_no[:2])
    print(f"[OK] Search completed, count={search.get('count', 0)}")

    out_file = Path(args.output_dir) / f"{bind_id or id_no}.jpeg"
    downloaded = download_id_via_ui(token, user_id, bind_id, id_no, out_file)
    print(f"[OK] Downloaded ID file: {downloaded}")

    if not args.no_split:
        front, back = split_combined_id_image(downloaded)
        print(f"[OK] Split front file: {front}")
        print(f"[OK] Split back file: {back}")


if __name__ == "__main__":
    main()
