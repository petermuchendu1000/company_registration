"""
Master pipeline orchestrator.

Stages (run in order, all idempotent):
  1. check  — verify public IP is whitelisted in Namecheap
  2. match  — match registered Namecheap domains to companies in Excel
  3. email  — create dev@domain forwarding for every company domain
  4. build  — generate brand overlays, build APK/AAB, generate all assets

Usage:
    python pipeline.py                          # Run all stages
    python pipeline.py --stage match            # Single stage
    python pipeline.py --stage build            # Build only
    python pipeline.py --company 02591663       # Build one company
    python pipeline.py --skip-build             # Regenerate assets only (no Gradle)
    python pipeline.py --stage build --artifact aab  # AAB only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent

import db  # Supabase (PostgreSQL) data-access layer — source of truth

# ── helpers ─────────────────────────────────────────────────────────────────

def _banner(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _load_env() -> None:
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# ── Stage 1: IP check ────────────────────────────────────────────────────────

def stage_check() -> None:
    _banner("STAGE 1 — IP check")
    from check_ip import ensure_ip_whitelisted
    ensure_ip_whitelisted(verbose=True)
    print("  Namecheap API: OK")


# ── Stage 2: Domain matching ─────────────────────────────────────────────────

def stage_match(dry_run: bool = False) -> None:
    _banner("STAGE 2 — Domain matching")
    import requests
    import xml.etree.ElementTree as ET
    import re

    API_URL = "https://api.namecheap.com/xml.response"
    NS = {"nc": "http://api.namecheap.com/xml.response"}

    _STOPWORDS = {
        "ltd", "limited", "plc", "cic", "llc", "uk", "the", "and", "of",
        "for", "services", "group", "solutions", "holdings", "enterprises",
        "international", "global", "management", "consulting", "associates",
    }

    def _normalize(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        words = [w for w in s.split() if w not in _STOPWORDS and len(w) > 1]
        return " ".join(words)

    def _domain_core(domain: str) -> str:
        sld = domain.lower().split(".")[0]
        return re.sub(r"[^a-z0-9]", "", sld)

    def _score(company_norm: str, dom_core: str) -> float:
        if not dom_core:
            return 0.0
        company_core = company_norm.replace(" ", "")
        if dom_core in company_core:
            return 0.9
        if len(dom_core) >= 7 and all(c in company_core for c in dom_core):
            pos = 0; seq = 0
            for ch in dom_core:
                idx = company_core.find(ch, pos)
                if idx >= 0:
                    seq += 1; pos = idx + 1
            if seq / len(dom_core) >= 0.85:
                return seq / len(dom_core)
        company_words = set(company_norm.split())
        dom_words = set(re.findall(r"[a-z]{3,}", dom_core))
        if dom_words:
            overlap = len(company_words & dom_words) / len(dom_words)
            if overlap >= 0.5:
                return overlap
        return 0.0

    base = {
        "ApiUser":  os.environ["NAMECHEAP_API_USER"],
        "ApiKey":   os.environ["NAMECHEAP_API_KEY"],
        "UserName": os.environ["NAMECHEAP_USERNAME"],
        "ClientIp": os.environ["NAMECHEAP_CLIENT_IP"],
    }

    r = requests.get(API_URL, params={**base, "Command": "namecheap.domains.getList", "PageSize": "100"}, timeout=30)
    root = ET.fromstring(r.text)
    domains = [d.get("Name") for d in root.findall(".//nc:Domain", NS) if d.get("Name")]
    print(f"  Namecheap domains: {domains}")

    # Load companies from Supabase
    companies = []   # (cn, name, existing_domain)
    for _r in db.all_rows(db.current_tenant_id()):
        cn   = str(_r.get("Company Number") or "").strip()
        name = str(_r.get("Company Name") or "").strip()
        dom  = str(_r.get("Domain") or "").strip()
        if cn and name:
            companies.append((cn, name, dom))

    written = 0
    for domain in domains:
        dom_core   = _domain_core(domain)
        best_score = 0.0
        best_cn    = ""
        best_name  = ""
        for cn, name, existing in companies:
            score = _score(_normalize(name), dom_core)
            if score > best_score:
                best_score = score; best_cn = cn; best_name = name

        if best_score >= 0.5:
            print(f"  MATCH  {domain!r:42s} -> {best_cn} {best_name!r}  (score={best_score:.2f})")
            if not dry_run:
                for cn, name, existing in companies:
                    if cn == best_cn and not existing:
                        db.update_row(db.current_tenant_id(), cn, {"Domain": domain})
                        # Update in-memory list too so next iteration sees it
                        companies = [
                            (c, n, domain if c == best_cn else d)
                            for c, n, d in companies
                        ]
                        written += 1
                        break
        else:
            print(f"  SKIP   {domain!r:42s} (best score={best_score:.2f})")

    if dry_run:
        print("\n  [Dry run — not writing]")
    else:
        print(f"\n  {written} new domain(s) written to Supabase.")


# ── Stage 3: Dev email setup ─────────────────────────────────────────────────

def _company_app_dir(company_number: str, company_name: str) -> Path:
    """pipeline_output/companies/{cn} - {name}/app — the current layout."""
    safe = re.sub(r'[\\/:*?"<>|]', "", company_name).strip()
    folder = f"{company_number} - {safe}" if safe else company_number
    return BASE_DIR / "pipeline_output" / "companies" / folder / "app"


def _manifest_path(company_number: str, company_name: str) -> Path | None:
    """The app manifest, current layout first, legacy second."""
    candidates = [
        _company_app_dir(company_number, company_name) / "manifest.json",
        BASE_DIR / "pipeline_output" / "apps" / company_number / "manifest.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def stage_email(dry_run: bool = False, company_numbers: list[str] | None = None) -> None:
    """
    Point dev@<domain> at the Gmail assigned to the company.

    That address is the developer contact on the Play listing, so a silent
    failure here is a blocked submission later.

    This used to re-implement the Namecheap calls inline — its own parameter
    building, its own XML parsing, its own API URL — against
    api.namecheap.com hardcoded, which ignored NAMECHEAP_SANDBOX, and with no
    retry on a rate limit. namecheap_automation already does all of that, and
    preserves any other mailboxes on the domain, so the stage calls it.
    """
    _banner("STAGE — dev@ email forwarding")
    from namecheap_automation import ensure_dev_email_forwarding

    tenant_id = db.current_tenant_id()
    wanted = set(company_numbers or [])

    companies = []
    for r in db.all_rows(tenant_id):
        cn     = str(r.get("Company Number") or "").strip()
        name   = str(r.get("Company Name") or "").strip()
        domain = str(r.get("Domain") or "").strip()
        email  = str(r.get("Assigned Email") or "").strip()
        if not cn or (wanted and cn not in wanted):
            continue
        if domain and email:
            companies.append((cn, name, domain, email))

    if not companies:
        print("  No company has both a domain and an assigned email yet.")
        return

    print(f"  {len(companies)} company(ies) with a domain and an assigned email")

    configured = skipped = failed = 0
    for cn, name, domain, assigned_email in companies:
        dev_email = f"dev@{domain}"
        print(f"\n  [{cn}] {domain}")

        if dry_run:
            print(f"    DRY — would ensure {dev_email} -> {assigned_email}")
        else:
            result = ensure_dev_email_forwarding(domain, assigned_email)
            error = str(result.get("error") or "")
            if result.get("status") == "configured":
                print(f"    OK  — {dev_email} -> {assigned_email}")
                configured += 1
            elif "dns" in error.lower():
                # The domain's DNS is not with Namecheap, so it cannot host
                # the forward. Not a failure of this run; the operator has to
                # move DNS or set the forward wherever it lives.
                print(f"    SKIP — {domain} uses external DNS; forwarding must be set there")
                skipped += 1
                continue
            else:
                print(f"    ERROR — {error or result.get('message') or 'unknown'}")
                failed += 1
                continue

        # The app manifest carries support_email into the Play listing. This
        # only ever looked in pipeline_output/apps/{cn}, a layout nothing has
        # written since the per-company folders landed, so the file was never
        # found and the update silently did nothing.
        manifest_path = _manifest_path(cn, name)
        if manifest_path is None:
            print(f"    note — no app manifest yet; run the app stage to pick up {dev_email}")
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"    WARN — could not read {manifest_path.name}: {e}")
            continue
        if data.get("support_email") == dev_email:
            print(f"    OK  — manifest already has {dev_email}")
        elif dry_run:
            print(f"    DRY — manifest support_email: {data.get('support_email')!r} -> {dev_email!r}")
        else:
            data["support_email"] = dev_email
            manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"    UPD — manifest support_email -> {dev_email}")

    if not dry_run:
        print(f"\n  {configured} configured, {skipped} skipped (external DNS), {failed} failed")


# ── Stage 4: Director ID (XBINDER) ──────────────────────────────────────────

def stage_director_id(
    company_numbers: list[str] | None = None,
    dry_run: bool = False,
    headless: bool = True,
) -> None:
    _banner("STAGE 4 — Director ID (XBINDER)")

    xbinder_email    = os.environ.get("XBINDER_EMAIL", "").strip()
    xbinder_password = os.environ.get("XBINDER_PASSWORD", "").strip()
    if not xbinder_email or not xbinder_password:
        print("  SKIP — set XBINDER_EMAIL and XBINDER_PASSWORD in .env")
        return

    import requests as req_lib
    sys.path.insert(0, str(BASE_DIR))
    from xbinder_auto import (
        login, create_bind, search_bind, download_id_via_ui,
        split_combined_id_image, load_director_profile,
    )

    COMPANIES_DIR = BASE_DIR / "pipeline_output" / "companies"
    CH_BASE       = "https://api.company-information.service.gov.uk"
    ch_key        = os.environ.get("COMPANIES_HOUSE_API_KEY", "")

    import re as _re
    def _co_dir(cn: str, name: str) -> Path:
        safe   = _re.sub(r'[\\/:*?"<>|]', "", name).strip()
        folder = f"{cn} - {safe}" if safe else cn
        return COMPANIES_DIR / folder

    # ── Load director candidates from Supabase ──────────────────────────────
    candidates: list[tuple[str, str, str, str, str]] = []  # (cn, director_name, dob_str, director_gender, co_name)
    for _row in db.all_rows(db.current_tenant_id()):
        cn = str(_row.get("Company Number") or "").strip()
        if not cn:
            continue
        if cn.isdigit() and len(cn) < 8:
            cn = cn.zfill(8)
        if company_numbers and cn not in company_numbers:
            continue
        co_name  = str(_row.get("Company Name") or "").strip()
        id_dir   = _co_dir(cn, co_name) / "director_id"
        if (id_dir / "ID Front.jpeg").exists():
            print(f"  [{cn}] OK — ID already downloaded")
            continue

        dirs = str(_row.get("Directors") or "").strip()
        nats = str(_row.get("Director Nationalities") or "").strip()
        dobs = str(_row.get("Director DOB") or "").strip()
        gens = str(_row.get("Director Gender") or "").strip()

        if not dirs:
            continue

        # Pick the first Kenyan director
        dir_list  = [d.strip() for d in dirs.split(";") if d.strip()]
        nat_list  = [n.strip() for n in nats.split(";")]
        dob_list  = [d.strip() for d in dobs.split(";")]
        gen_list  = [g.strip() for g in gens.split(";")]

        director_name   = ""
        director_dob    = ""
        director_gender = ""
        for idx, nat in enumerate(nat_list):
            if "kenyan" in nat.lower():
                director_name   = dir_list[idx] if idx < len(dir_list) else ""
                director_dob    = dob_list[idx] if idx < len(dob_list) else ""
                director_gender = gen_list[idx] if idx < len(gen_list) else ""
                break

        if not director_name:
            continue  # no Kenyan director

        candidates.append((cn, director_name, director_dob, director_gender, co_name))

    # ── Backfill DOB from CH API for any candidate missing it ────────────────
    if not ch_key and any(not dob for _, _, dob, _, _ in candidates):
        print("  WARN — COMPANIES_HOUSE_API_KEY not set; DOB will be randomised for missing entries")

    session_ch = req_lib.Session()
    filled: list[tuple[str, str, str, str, str]] = []
    for cn, director_name, director_dob, director_gender, co_name in candidates:
        if director_dob:
            filled.append((cn, director_name, director_dob, director_gender, co_name))
            continue
        # DOB missing — query CH API, match by surname
        dob_str = ""
        if ch_key:
            try:
                resp = session_ch.get(
                    f"{CH_BASE}/company/{cn}/officers",
                    auth=(ch_key, ""),
                    timeout=20,
                )
                if resp.status_code == 200:
                    target_full = director_name.strip().upper()
                    target_surn = target_full.split(",")[0].strip()
                    surn_fallback = ""
                    for o in resp.json().get("items", []):
                        if o.get("resigned_on"):
                            continue
                        if "director" not in (o.get("officer_role", "") or "").lower():
                            continue
                        dob = o.get("date_of_birth") or {}
                        y, m, day = dob.get("year"), dob.get("month"), dob.get("day")
                        if y and m and day:
                            d_val = f"{int(y):04d}-{int(m):02d}-{int(day):02d}"
                        elif y and m:
                            d_val = f"{int(y):04d}-{int(m):02d}"
                        elif y:
                            d_val = f"{int(y):04d}"
                        else:
                            d_val = ""
                        ch_full = (o.get("name") or "").strip().upper()
                        ch_surn = ch_full.split(",")[0].strip()
                        if ch_full == target_full:
                            dob_str = d_val
                            break
                        if ch_surn == target_surn and not surn_fallback:
                            surn_fallback = d_val
                    if not dob_str:
                        dob_str = surn_fallback
            except Exception as e:
                print(f"  [{cn}] CH API DOB lookup error: {e}")
        filled.append((cn, director_name, dob_str, director_gender, co_name))
        if dob_str:
            print(f"  [{cn}] DOB from CH API: {dob_str}")
        else:
            print(f"  [{cn}] DOB not found — will be randomised by load_director_profile")

    candidates = filled

    if not candidates:
        print("  No companies with Kenyan directors found (or all IDs already downloaded).")
        return

    print(f"\n  {len(candidates)} companies need director IDs")

    if dry_run:
        for cn, name, _, gender, _ in candidates:
            print(f"  [{cn}] DRY — would generate ID for: {name}  gender={gender or '?'}")
        return

    # ── Login to XBINDER once ─────────────────────────────────────────────────
    login_session = req_lib.Session()
    try:
        auth    = login(login_session, xbinder_email, xbinder_password)
        token   = auth["token"]
        user_id = auth.get("user_id")
        print("  XBINDER login: OK")
    except Exception as e:
        print(f"  XBINDER login FAILED: {e}")
        return

    # ── Process each company ──────────────────────────────────────────────────
    for cn, director_name, dob_str, director_gender, co_name in candidates:
        id_dir = _co_dir(cn, co_name) / "director_id"
        if (id_dir / "ID Front.jpeg").exists():
            print(f"  [{cn}] OK — ID already downloaded")
            continue

        # Normalise gender: M/F only; fall back to infer from name if still unknown
        gender_val = director_gender.strip().upper()
        if gender_val not in {"M", "F"}:
            from run_pipeline import _infer_gender as _ig
            gender_val = _ig(director_name)
        if gender_val not in {"M", "F"}:
            gender_val = "M"   # last-resort default

        print(f"\n  [{cn}] Generating ID for: {director_name}  gender={gender_val}")

        # Build profile via temp JSON + load_director_profile (proven pattern)
        profile_data = {
            cn: {
                "company_number": cn,
                "xbinder_profile": {
                    "fname": "",
                    "lname": "",
                    "sname": "",
                    "idnumber": "",
                    "actualdob": dob_str,
                    "gender": gender_val,
                },
                "company_data": {
                    "primary_director": {
                        "name": director_name,
                        "date_of_birth": dob_str,
                    }
                },
            }
        }
        temp_profile = BASE_DIR / "pipeline_output" / f"_tmp_profile_{cn}.json"
        try:
            temp_profile.write_text(json.dumps(profile_data), encoding="utf-8")
            profile = load_director_profile(temp_profile, cn)
        except Exception as e:
            print(f"  [{cn}] profile build FAILED: {e}")
            temp_profile.unlink(missing_ok=True)
            continue
        finally:
            temp_profile.unlink(missing_ok=True)

        # Create bind (new session per bind, Bearer-token authenticated)
        try:
            bind_session = req_lib.Session()
            bind_result = create_bind(bind_session, token, profile)
            bind    = bind_result.get("bind", {})
            bind_id = bind.get("id", "")
            id_no   = bind.get("idNo", profile.get("idnumber", ""))
            print(f"  [{cn}] bind: id={bind_id}  id_no={id_no}")
            print(f"  [{cn}] name: {profile['fname']} {profile['sname']} {profile['lname']}")
        except Exception as e:
            print(f"  [{cn}] create_bind FAILED: {e}")
            continue

        if not bind_id:
            print(f"  [{cn}] SKIP — no bind_id in response")
            continue

        # Search confirms bind is indexed (mirrors xbinder_auto main() flow)
        try:
            search_bind(bind_session, token, id_no[:2] if id_no else "")
        except Exception:
            pass

        # Download via browser UI
        id_dir.mkdir(parents=True, exist_ok=True)
        combined = id_dir / "ID_Combined.jpeg"
        try:
            download_id_via_ui(token, user_id, bind_id, id_no, combined, headless=headless)
        except Exception as e:
            print(f"  [{cn}] download FAILED: {e}")
            continue

        # Split into front/back
        try:
            front_path, back_path = split_combined_id_image(combined)
            print(f"  [{cn}] ID done: {front_path.name} + {back_path.name}")
        except Exception as e:
            print(f"  [{cn}] split FAILED (keeping combined): {e}")
            front_path = combined
            back_path  = combined

        # Persist metadata
        result = {
            "company_number":  cn,
            "director_name":   director_name,
            "full_name_on_id": f"{profile.get('fname','')} {profile.get('sname','')} {profile.get('lname','')}".strip(),
            "id_type":         "National ID",
            "id_country":      "KE",
            "bind_id":         bind_id,
            "id_image_front":  str(front_path),
            "id_image_back":   str(back_path),
            "status":          "downloaded",
        }
        (id_dir / "id_result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(f"  [{cn}] Saved id_result.json")


# ── Stage 5: App build ──────────────────────────────────────────────────────

def stage_build(
    company_numbers: list[str] | None = None,
    skip_build: bool = False,
    artifact: str = "both",
) -> None:
    _banner("STAGE 5 — App build")

    sys.path.insert(0, str(BASE_DIR))
    from apps.generator import toolchain

    sdk = toolchain.sdk_dir()
    # Only export JAVA_HOME when one was actually found. The old code wrote a
    # hardcoded Windows path into the environment unconditionally, which on
    # any other machine pointed Gradle at a directory that does not exist.
    java_home = toolchain.java_home()
    if java_home:
        os.environ["JAVA_HOME"] = java_home
    else:
        print("  WARN: no JDK found (JAVA_HOME unset and no java on PATH) — "
              "Gradle will likely fail")

    from apps.generator.catalog import load_companies
    from apps.generator.generate import (
        build_flavors, run_gradle, collect_artifacts,
        backup_and_make_assets, _ensure_local_properties,
    )

    companies = load_companies(
        company_numbers=company_numbers,
        limit=None,
    )
    # Only build companies that have a domain
    companies = [c for c in companies if c.domain]
    if not companies:
        print("  No companies with domains — nothing to build.")
        return

    print(f"  Building {len(companies)} company app(s):")
    for c in companies:
        print(f"    {c.company_number}  {c.display_name}  ({c.domain})")

    _ensure_local_properties(sdk)

    built = build_flavors(companies)
    print(f"  Wrote {len(built)} flavor overlays + build.gradle.kts")

    if skip_build:
        print("  Gradle: SKIPPED (--skip-build)")
        from apps.generator.generate import collect_artifacts
        got = collect_artifacts(built, variant="release", artifacts=artifact)
        backup_and_make_assets(built, got)
        return

    rc = run_gradle(built, variant="Release", artifact=artifact)
    if rc != 0:
        print(f"  Gradle FAILED (exit {rc})")
        sys.exit(rc)

    got = collect_artifacts(built, variant="release", artifacts=artifact)
    print(f"  Collected {len(got)}/{len(built)} artifact set(s)")

    backup_and_make_assets(built, got)
    print("  Assets generated.")


# ── Summary ──────────────────────────────────────────────────────────────────

def print_summary() -> None:
    _banner("PIPELINE SUMMARY")
    apps_dir = BASE_DIR / "pipeline_output" / "apps"
    if not apps_dir.exists():
        print("  No pipeline_output/apps directory found.")
        return

    for cn_dir in sorted(apps_dir.iterdir()):
        if not cn_dir.is_dir():
            continue
        manifest_path = cn_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        apk_ok  = "OK" if m.get("apk") and (cn_dir / m["apk"]).exists() else "--"
        aab_ok  = "OK" if m.get("aab") and (cn_dir / m["aab"]).exists() else "--"
        pp_ok   = "OK" if m.get("privacy_policy_url") else "--"
        dom_ok  = "OK" if m.get("domain") else "--"
        id_ok   = "OK" if (BASE_DIR / "pipeline_output" / "representative_ids" / cn_dir.name / "ID Front.jpeg").exists() else "--"
        email   = m.get("support_email", "")
        print(f"  {cn_dir.name}  APK:{apk_ok}  AAB:{aab_ok}  Policy:{pp_ok}  Domain:{dom_ok}  ID:{id_ok}  email={email}")


# ── Stage: DUNS lookup ───────────────────────────────────────────────────────

def stage_duns(company_numbers: list[str] | None = None, dry_run: bool = False) -> None:
    """
    Fetch a D-U-N-S number for every company that has none.

    The dashboard has run this per company for a while; the pipeline could
    not, so a batch run produced apps for companies with no DUNS and the gap
    was only visible one row at a time.
    """
    _banner("STAGE — DUNS lookup")
    from run_pipeline import get_duns_number

    tenant_id = db.current_tenant_id()
    wanted = set(company_numbers or [])
    todo = []
    for row in db.all_rows(tenant_id):
        cn = str(row.get("Company Number") or "").strip()
        if not cn or (wanted and cn not in wanted):
            continue
        if str(row.get("DUNS Number") or "").strip():
            continue
        todo.append((cn, str(row.get("Company Name") or "")))

    if not todo:
        print("  Every company already has a DUNS number — nothing to do.")
        return

    print(f"  {len(todo)} company(ies) without a DUNS number:")
    for cn, name in todo:
        print(f"    {cn}  {name}")
    if dry_run:
        print("  --dry-run: no lookups performed.")
        return

    found = submitted = failed = 0
    for cn, name in todo:
        try:
            r = get_duns_number(cn, headless=True) or {}
        except Exception as e:  # noqa: BLE001 - one bad lookup must not stop the batch
            print(f"    {cn}  ERROR  {type(e).__name__}: {e}")
            failed += 1
            continue

        if r.get("duns_number"):
            db.update_row(tenant_id, cn, {
                "DUNS Number": r["duns_number"], "DUNS Status": "found",
            })
            print(f"    {cn}  {r['duns_number']}")
            found += 1
        elif r.get("status") == "submitted":
            db.update_row(tenant_id, cn, {
                "DUNS Status": "submitted",
                "DUNS Email Used": r.get("temp_email", ""),
            })
            print(f"    {cn}  request submitted ({r.get('temp_email', '')})")
            submitted += 1
        else:
            print(f"    {cn}  not found  {r.get('error') or r.get('status') or ''}")
            failed += 1

    print(f"\n  {found} found, {submitted} submitted, {failed} unresolved")


# ── Stage: Play publish ──────────────────────────────────────────────────────

def stage_publish(
    company_numbers: list[str] | None = None,
    track: str = "internal",
    commit: bool = False,
) -> None:
    """
    Push each built app to Google Play.

    Dry run unless `commit` is True.

    A dry run is not a local check. It needs Play credentials, uploads the
    real bundle and listing into a draft edit, has Play validate it, then
    deletes the edit so nothing is released. It is the only way to learn that
    Play would reject a listing, an AAB or a Data Safety answer before a
    release depends on it — but it spends bandwidth, API quota and most
    likely a version code.

    Committing is outward-facing and effectively irreversible, so it never
    happens implicitly.
    """
    _banner(
        f"STAGE — Play publish ({'COMMIT — RELEASES TO PLAY' if commit else 'dry run — uploads, validates, discards'}, "
        f"track={track})"
    )

    import play_publisher

    tenant_id = db.current_tenant_id()
    wanted = set(company_numbers or [])
    targets = []
    for row in db.all_rows(tenant_id):
        cn = str(row.get("Company Number") or "").strip()
        if not cn or (wanted and cn not in wanted):
            continue
        targets.append((cn, str(row.get("Company Name") or "")))

    if not targets:
        print("  No companies selected.")
        return

    ok = blocked = errored = 0
    for cn, name in targets:
        print(f"\n  {cn}  {name}")
        try:
            rep = play_publisher.publish(cn, track=track, dry_run=not commit)
        except Exception as e:  # noqa: BLE001 - one failure must not stop the batch
            print(f"    ERROR  {type(e).__name__}: {e}")
            errored += 1
            continue

        for w in rep.warnings:
            print(f"    warn   {w}")
        if not rep.ok:
            for err in rep.errors:
                print(f"    BLOCK  {err}")
            blocked += 1
            continue

        if rep.committed:
            print(f"    published  {rep.package_name} v{rep.version_code} -> {rep.track}")
        else:
            print(f"    validated  {rep.package_name} v{rep.version_code} "
                  f"(uploaded to a draft edit, then discarded — not released)")
        ok += 1

    verb = "published" if commit else "validated"
    print(f"\n  {ok} {verb}, {blocked} blocked, {errored} errored")


# ── Entry point ──────────────────────────────────────────────────────────────

STAGES = ["check", "match", "duns", "email", "director_id", "build", "publish"]


def main() -> None:
    p = argparse.ArgumentParser(description="Company app pipeline")
    p.add_argument("--stage",       choices=STAGES, help="Run a single stage only")
    p.add_argument("--company",     nargs="+", metavar="CN", help="Limit build to these company numbers")
    p.add_argument("--skip-build",  action="store_true", help="Skip Gradle (regenerate assets only)")
    p.add_argument("--artifact",    default="both", choices=["apk", "aab", "both"])
    p.add_argument("--dry-run",     action="store_true", help="Preview changes without writing")
    p.add_argument("--no-headless", action="store_true", help="Show browser window during XBINDER download (debug)")
    p.add_argument("--tenant",      metavar="SLUG",
                   help="Tenant whose companies to process. Defaults to the tenant already "
                        "bound to this process, or SINGLE_TENANT_SLUG.")
    p.add_argument("--track",       default="internal",
                   help="Play track for the publish stage (default: internal)")
    p.add_argument("--commit",      action="store_true",
                   help="Publish stage: actually release to Play. Without it the bundle is "
                        "still uploaded to a draft edit and validated, then discarded.")
    args = p.parse_args()

    _load_env()

    # Committing is outward-facing and a version code cannot be reused, so it
    # has to be asked for on its own, not swept along by a whole-pipeline run.
    if args.commit and args.stage != "publish":
        raise SystemExit(
            "--commit releases to Google Play and cannot be combined with a full run.\n"
            "Run it deliberately:  python pipeline.py --stage publish --commit"
        )

    if args.tenant:
        tenant = db.get_tenant_by_slug(args.tenant)
        if not tenant:
            raise SystemExit(f"No such tenant: {args.tenant}")
        db.set_current_tenant(tenant["id"])

    run = args.stage or "all"
    t0  = time.time()

    if run in ("all", "check"):
        stage_check()

    if run in ("all", "match"):
        stage_match(dry_run=args.dry_run)

    if run in ("all", "duns"):
        stage_duns(company_numbers=args.company, dry_run=args.dry_run)

    if run in ("all", "email"):
        stage_email(dry_run=args.dry_run, company_numbers=args.company)

    if run in ("all", "director_id"):
        stage_director_id(
            company_numbers=args.company,
            dry_run=args.dry_run,
            headless=not args.no_headless,
        )

    if run in ("all", "build"):
        stage_build(
            company_numbers=args.company,
            skip_build=args.skip_build,
            artifact=args.artifact,
        )

    if run in ("all", "publish"):
        # In a full run this is a validation pass: it reports what Play would
        # reject without sending anything. See the --commit guard above.
        stage_publish(
            company_numbers=args.company,
            track=args.track,
            commit=args.commit and run == "publish",
        )

    print_summary()
    print(f"\n  Total time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
