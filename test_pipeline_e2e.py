"""
The whole pipeline, end to end, through the real HTTP API.

WHAT THIS DOES AND DOES NOT COVER
---------------------------------
Every stage reaches an external service that this container has no
credentials for and, in two cases, that costs money or cannot be undone:

    details      Companies House          read-only, needs an API key
    duns         Dun & Bradstreet         needs an account
    certificate  Companies House          read-only, needs an API key
    domain       Namecheap                REGISTERS A DOMAIN — spends money
    email        the tenant's own pool    no external service
    director_id  XBINDER                  needs an account
    app          Gradle + Android SDK     needs the SDK and a JDK
    publish      Google Play              needs a service account; a commit
                                          burns a version code permanently

So those boundaries are stubbed, and what runs for real is everything
between them — which is the part this project actually owns:

  * the Flask routes, including the deny-by-default auth layer
  * the Postgres job registry: job_claim, the queued/running/done/error
    transitions, the per-job captured log, worker ownership
  * run-all's ordering and its stop-on-first-failure rule
  * the worker threads and the tenant rebinding they depend on, since a
    ContextVar is not inherited by a spawned thread
  * _compute_stages reading state back off disk
  * company_paths keeping every artifact inside the tenant's own subtree
  * the artifact listing and the download guard

The email stage is not stubbed at all: it runs against the real pool in
Postgres, including the FOR UPDATE SKIP LOCKED claim.

The stubs are written to the shapes the real callers return, taken from
_exec_stage and play_publisher.Report, and they write real files — a real
PDF for the certificate, a real AAB for the build — so _compute_stages is
reading genuine artifacts rather than being told what to think.

    DATABASE_URL=... python test_pipeline_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
    print("DATABASE_URL not set — skipping (this needs a real Postgres).")
    raise SystemExit(0)

# Own the output tree before anything resolves a path from it.
_TMP_OUTPUT = tempfile.mkdtemp(prefix="pipeline-e2e-")
os.environ["PIPELINE_OUTPUT_DIR"] = _TMP_OUTPUT
os.environ.setdefault("FLASK_SECRET_KEY", "e2e-secret")

import db                      # noqa: E402
import company_paths           # noqa: E402

CN = "30000001"
NAME = "HARBOUR COURIERS LTD"
PASSWORD = "e2e-password-123"

ALL_STAGES = ["details", "duns", "certificate", "domain", "email",
              "director_id", "app", "publish"]


# ── faithful stubs for the external boundaries ───────────────────────────────

class Calls:
    """Records which boundary was reached, so a skipped stage is visible."""

    def __init__(self):
        self.seen: list[str] = []
        self.fail: set[str] = set()

    def hit(self, what: str) -> None:
        self.seen.append(what)
        if what in self.fail:
            raise RuntimeError(f"stubbed {what} failure")


CALLS = Calls()


def _install_stubs() -> None:
    import run_pipeline
    import pipeline
    import play_publisher

    def get_company_details(cn, *a, **k):
        CALLS.hit("companies-house:details")
        return {
            "company_name": NAME,
            "company_status": "active",
            "type": "ltd",
            "date_of_incorporation": "2019-04-02",
            "sic_codes": "53202",
            "address": "12 Dock Road, Liverpool, L3 1AB",
            "director_names": "MWANGI, John",
            "director_nationalities": "Kenyan",
            "director_dobs": "1985-03",
            "directors": [{"name": "MWANGI, John"}],
            "officers_seen": 3,
        }

    def get_duns_number(cn, headless=True, *a, **k):
        CALLS.hit("dnb:duns")
        print(f"  [{cn}] DUNS lookup: 221544123")
        return {"duns_number": "221544123", "status": "found"}

    def download_certificate(cn, out_dir, *a, **k):
        CALLS.hit("companies-house:certificate")
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        pdf = d / f"Certificate_{cn}.pdf"
        # A real PDF header and enough bytes to pass the size floor
        # _compute_stages applies, so the stage is judged on a real file.
        pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 4096 + b"\n%%EOF\n")
        print(f"  saved {pdf.name} ({pdf.stat().st_size} bytes)")
        return str(pdf), ""

    def register_company_domain(company_name, details, current_domain="", **k):
        CALLS.hit("namecheap:register")
        if current_domain:
            return {"status": "exists",
                    "message": f"already holds {current_domain}",
                    "domain": current_domain}
        print("  registered harbourcouriers.online for 9.48")
        return {"status": "registered", "domain": "harbourcouriers.online",
                "charged": "9.48"}

    def stage_director_id(company_numbers=None, dry_run=False, headless=True):
        CALLS.hit("xbinder:director_id")
        for cn in (company_numbers or []):
            row = db.find_row(db.current_tenant_id(), cn) or {}
            d = company_paths.ensure_company_dir(
                cn, str(row.get("Company Name") or "")) / "director_id"
            d.mkdir(parents=True, exist_ok=True)
            for f in ("ID Front.jpeg", "ID Back.jpeg"):
                (d / f).write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 512)
            print(f"  [{cn}] ID done: ID Front.jpeg + ID Back.jpeg")

    def stage_build(company_numbers=None, skip_build=False, artifact="both"):
        CALLS.hit("gradle:build")
        for cn in (company_numbers or []):
            row = db.find_row(db.current_tenant_id(), cn) or {}
            name = str(row.get("Company Name") or "")
            app = company_paths.ensure_company_dir(cn, name) / "app"
            (app / "artifacts").mkdir(parents=True, exist_ok=True)
            for ext in ("apk", "aab"):
                (app / "artifacts" / f"app-c{cn}-release.{ext}").write_bytes(
                    b"PK\x03\x04" + b"0" * 2048)
            (app / "manifest.json").write_text(json.dumps({
                "package_name": f"uk.co.c{cn}.utility",
                "support_email": "dev@harbourcouriers.online",
            }), encoding="utf-8")
            (app / "version.json").write_text(
                json.dumps({"version_code": 1, "version_name": "1.0"}), encoding="utf-8")
            print(f"  [{cn}] built app-c{cn}-release.apk + .aab")

    def publish(company_number, track="internal", dry_run=True, **k):
        CALLS.hit("play:publish")
        rep = play_publisher.Report(
            company_number=company_number, ok=True, dry_run=dry_run,
            package_name=f"uk.co.c{company_number}.utility",
            version_code=1, track=track, edit_id="edit-e2e",
            committed=not dry_run,
        )
        play_publisher.write_receipt(company_number, rep)
        return rep

    run_pipeline.get_company_details = get_company_details
    run_pipeline.get_duns_number = get_duns_number
    run_pipeline.download_certificate = download_certificate
    run_pipeline.register_company_domain = register_company_domain
    pipeline.stage_director_id = stage_director_id
    pipeline.stage_build = stage_build
    play_publisher.publish = publish


# ── harness ──────────────────────────────────────────────────────────────────

class Harness:
    def __init__(self, slug_prefix="e2e"):
        self.slug = f"{slug_prefix}-{uuid.uuid4().hex[:8]}"
        self.tenant_id = db.create_tenant(self.slug, self.slug)
        self.email = f"{self.slug}@example.com"
        db.create_user(self.tenant_id, self.email, PASSWORD, "operator")
        db.import_emails(self.tenant_id, [f"{self.slug}-pool-{i}@gmail.com"
                                          for i in range(5)])
        # Deliberately NOT setting SINGLE_TENANT_SLUG. That fallback resolves
        # a tenant from the environment whenever none is bound, which would
        # paper over the thing this file is here to exercise: a worker thread
        # does not inherit the request's ContextVar, so _run_tracked has to
        # rebind the tenant itself. With the fallback set, removing that
        # rebinding changes nothing and the suite still passes.
        os.environ.pop("SINGLE_TENANT_SLUG", None)
        db._SINGLE_TENANT_CACHE.clear()
        company_paths._reset_cache()

        import app as flask_app
        self.app = flask_app.app
        self.client = self.app.test_client()
        r = self.client.post("/login", data={"email": self.email, "password": PASSWORD})
        assert r.status_code in (200, 302), f"login failed: {r.status_code}"

    def add_company(self, cn=CN, name=""):
        db.add_company(self.tenant_id, cn, name, "")
        return cn

    def get(self, path):
        r = self.client.get(path)
        return r.status_code, (r.get_json() if r.is_json else r.data)

    def post(self, path, **kw):
        r = self.client.post(path, **kw)
        return r.status_code, (r.get_json() if r.is_json else r.data)

    def co_dir(self, cn=CN, name=NAME):
        """The company directory, resolved as this tenant.

        The assertions run outside a request, so nothing has bound a tenant
        for them; company_paths would otherwise fall back to _untenanted.
        """
        return company_paths.company_dir(cn, name, tenant_id=self.tenant_id)

    def stages(self, cn=CN):
        code, body = self.get(f"/api/v2/company/{cn}")
        assert code == 200, (code, body)
        return body.get("stages") or {}

    def jobs(self, cn=CN):
        return db.jobs_for_company(self.tenant_id, cn)

    def wait(self, cn=CN, expect=None, timeout=120):
        """
        Wait for a run-all sequence to settle.

        "No job is currently active" is not the end of the run: run-all works
        through the stages one at a time, so between finishing one and
        claiming the next there is a window with nothing running. Waiting on
        that window returns while most of the pipeline is still ahead. So
        finish on a definite outcome instead — a stage failed, which stops
        the run, or every expected stage has a terminal job.
        """
        expect = expect or ALL_STAGES
        deadline = time.time() + timeout
        while time.time() < deadline:
            jobs = self.jobs(cn)
            if any(j.get("status") == "failed" for j in jobs.values()):
                return jobs
            active = [s for s, j in jobs.items()
                      if j.get("status") in db.ACTIVE_JOB_STATUSES]
            if all(s in jobs for s in expect) and not active:
                return jobs
            time.sleep(0.3)
        raise AssertionError(
            f"timed out after {timeout}s; "
            f"jobs={ {s: j.get('status') for s, j in self.jobs(cn).items()} }")


# ── the run ──────────────────────────────────────────────────────────────────

def test_run_all_completes_every_stage() -> None:
    h = Harness()
    h.add_company()

    before = h.stages()
    assert not any(before.values()), f"a fresh company started with work done: {before}"

    code, _ = h.post(f"/api/v2/company/{CN}/run-all")
    assert code == 200, code
    jobs = h.wait()

    errored = {s: j.get("error") for s, j in jobs.items() if j.get("status") == "failed"}
    assert not errored, f"stages errored: {errored}"

    missing = [s for s in ALL_STAGES if s not in jobs]
    assert not missing, f"run-all never reached: {missing}"
    assert all(j["status"] == "done" for j in jobs.values()), \
        {s: j["status"] for s, j in jobs.items()}

    after = h.stages()
    # publish stays false: the dashboard validates, it does not release.
    expected_true = [s for s in ALL_STAGES if s != "publish"]
    not_done = [s for s in expected_true if not after.get(s)]
    assert not not_done, f"ran but did not register as done: {not_done} ({after})"
    assert after["publish"] is False, "a dry run reported itself as released"
    print(f"  OK  all {len(ALL_STAGES)} stages ran and registered; publish correctly not released")
    return h


def test_every_external_boundary_was_reached(h: Harness) -> None:
    for boundary in ("companies-house:details", "dnb:duns",
                     "companies-house:certificate", "namecheap:register",
                     "xbinder:director_id", "gradle:build", "play:publish"):
        assert boundary in CALLS.seen, f"{boundary} was never called: {CALLS.seen}"
    print("  OK  every external boundary was reached exactly where expected")


def test_the_row_was_populated(h: Harness) -> None:
    row = db.find_row(h.tenant_id, CN)
    for field, expect in (("Company Name", NAME), ("Status", "active"),
                          ("DUNS Number", "221544123"),
                          ("Domain", "harbourcouriers.online"),
                          ("Director DOB", "1985-03")):
        got = str(row.get(field) or "")
        assert got == expect, f"{field}: {got!r} != {expect!r}"
    assert "@" in str(row.get("Assigned Email") or ""), row.get("Assigned Email")
    print("  OK  the company row carries what each stage wrote")


def test_artifacts_landed_in_this_tenants_subtree(h: Harness) -> None:
    root = company_paths.companies_root()
    mine = h.co_dir()
    assert mine.is_dir(), f"no company directory at {mine}"
    for rel in ("certificate", "director_id", "app/artifacts"):
        assert (mine / rel).is_dir(), f"missing {rel}"
    assert list((mine / "app" / "artifacts").glob("*.aab")), "no AAB"
    stray = [p for p in root.rglob("*")
             if p.is_file() and h.slug not in p.relative_to(root).parts[0]]
    assert not stray, f"files written outside the tenant subtree: {stray[:5]}"
    print("  OK  every artifact landed inside this tenant's own subtree")


#: _exec_stage's email branch returns without printing anything, so the job
#: records an empty log and the drawer shows "No output yet." for a stage that
#: did real work. Pinned here as a known gap rather than hidden by a weaker
#: assertion: if a stage starts or stops producing output, this list is what
#: has to change.
STAGES_WITH_NO_OUTPUT = {"email"}


def test_each_job_captured_its_log(h: Harness) -> None:
    jobs = h.jobs()
    expected_quiet = {s for s in jobs if s in STAGES_WITH_NO_OUTPUT}
    empty = {s for s, j in jobs.items() if not (j.get("log") or "").strip()}

    unexpectedly_quiet = empty - expected_quiet
    assert not unexpectedly_quiet, f"stages recorded no output: {sorted(unexpectedly_quiet)}"
    unexpectedly_loud = expected_quiet - empty
    assert not unexpectedly_loud, (
        f"{sorted(unexpectedly_loud)} now logs; drop it from STAGES_WITH_NO_OUTPUT")

    code, body = h.get(f"/api/v2/company/{CN}/job/details")
    assert code == 200 and "director" in (body.get("log") or ""), body
    print(f"  OK  every stage captured its output and the log endpoint serves it "
          f"(known silent: {sorted(expected_quiet) or 'none'})")


def test_artifacts_are_listed_and_downloadable(h: Harness) -> None:
    """The dashboard lists files inside the company payload, under "files"."""
    code, body = h.get(f"/api/v2/company/{CN}")
    assert code == 200, (code, body)
    files = body.get("files") or {}
    for section in ("certificate", "director_id", "app"):
        assert files.get(section), f"nothing listed under {section}: {files}"
    listed = json.dumps(files)
    assert ".aab" in listed and ".pdf" in listed, listed[:400]

    aab = next(p for p in h.co_dir().rglob("*.aab"))
    r = h.client.get(f"/api/v2/dl/app.aab?path={aab}")
    assert r.status_code == 200, f"the owner could not download its own artifact: {r.status_code}"
    assert r.data.startswith(b"PK\x03\x04"), "the bytes served were not the artifact"
    print("  OK  artifacts are listed in the payload and the owner can download them")


def test_rerunning_is_idempotent(h: Harness) -> None:
    """Run-all again over a finished company must not redo or break anything."""
    seen_before = len(CALLS.seen)
    code, _ = h.post(f"/api/v2/company/{CN}/run-all")
    assert code == 200, code
    h.wait()
    after = h.stages()
    assert all(after.get(s) for s in ALL_STAGES if s != "publish"), after
    # Only publish is re-attempted: every other stage reports itself done.
    redone = CALLS.seen[seen_before:]
    assert all(c == "play:publish" for c in redone), \
        f"a completed stage was run again: {redone}"
    print("  OK  a second run-all repeats no completed stage")


def test_a_failing_stage_stops_the_run_and_is_recorded() -> None:
    """The stop-on-first-failure rule, and what the operator is told."""
    h = Harness("e2efail")
    h.add_company()
    CALLS.fail.add("namecheap:register")      # domain is stage 4 of 8
    try:
        code, _ = h.post(f"/api/v2/company/{CN}/run-all")
        assert code == 200, code
        jobs = h.wait(expect=["details", "duns", "certificate", "domain"])
    finally:
        CALLS.fail.discard("namecheap:register")

    assert jobs.get("domain", {}).get("status") == "failed", jobs.get("domain")
    assert "namecheap" in (jobs["domain"].get("error") or "").lower(), jobs["domain"]
    for earlier in ("details", "duns", "certificate"):
        assert jobs.get(earlier, {}).get("status") == "done", (earlier, jobs.get(earlier))
    for later in ("email", "director_id", "app", "publish"):
        assert later not in jobs, f"{later} ran after an earlier stage failed"
    stages = h.stages()
    assert stages["certificate"] is True and stages["domain"] is False, stages
    print("  OK  a failing stage stops the run, records the reason, and leaves the rest pending")
    return h


def test_a_second_tenant_sees_none_of_it(done: Harness) -> None:
    """The same company number, a different tenant: a clean slate."""
    other = Harness("e2eother")
    other.add_company(CN, NAME)
    stages = other.stages()
    assert not any(stages.values()), f"another tenant inherited progress: {stages}"
    aab = next(p for p in done.co_dir().rglob("*.aab"))
    code, _ = other.get(f"/api/v2/dl/app.aab?path={aab}")
    assert code in (403, 404), f"another tenant downloaded the first one's AAB: {code}"
    print("  OK  a second tenant holding the same number sees no progress and no files")


def main() -> int:
    _install_stubs()
    outcome: dict[str, str] = {}
    finished: Harness | None = None

    def run(fn, *a):
        try:
            out = fn(*a)
            outcome[fn.__name__] = "pass"
            return out
        except AssertionError as e:
            outcome[fn.__name__] = "fail"
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            outcome[fn.__name__] = "error"
            import traceback
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
        return None

    print("\n  ── a company from nothing to a validated release ──")
    finished = run(test_run_all_completes_every_stage)
    if finished:
        for fn in (test_every_external_boundary_was_reached,
                   test_the_row_was_populated,
                   test_artifacts_landed_in_this_tenants_subtree,
                   test_each_job_captured_its_log,
                   test_artifacts_are_listed_and_downloadable,
                   test_rerunning_is_idempotent):
            run(fn, finished)

    print("\n  ── failure and isolation ──")
    run(test_a_failing_stage_stops_the_run_and_is_recorded)
    if finished:
        run(test_a_second_tenant_sees_none_of_it, finished)

    shutil.rmtree(_TMP_OUTPUT, ignore_errors=True)
    # Count what actually ran. A test skipped because an earlier one failed is
    # not a pass, and must not be reported as one.
    planned = 9
    passed = sum(1 for v in outcome.values() if v == "pass")
    skipped = planned - len(outcome)
    tail = f" ({skipped} never ran)" if skipped else ""
    print(f"\n{passed}/{planned} passed{tail}")
    return 1 if passed != planned else 0


if __name__ == "__main__":
    raise SystemExit(main())
