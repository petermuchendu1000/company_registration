"""
Tenancy of the background job runner.

Three defects these cover, all of which failed silently:

  1. A stage job runs in a new thread, and Flask binds the tenant in a
     ContextVar. A new thread starts with an empty context, so the binding was
     lost: the job either raised, or fell back to SINGLE_TENANT_SLUG and wrote
     a different client's row.
  2. Job state was keyed by company number alone. The primary key is
     (tenant_id, company_number), so two clients can hold the same company and
     were sharing job state — and each other's captured stdout, which carries
     directors, emails and DUNS numbers.
  3. The job-status route returned that stdout without checking who owned the
     company.

Needs a database: the job registry lives in Postgres (see test_jobs_store.py
for the storage behaviour itself). These cover the layer above it — that the
worker thread, the route and the build queue all use it correctly.

    DATABASE_URL=postgresql://... python test_job_tenancy.py
"""

from __future__ import annotations

import os
import threading
import time

import api_v2
import db

TENANTS: dict[str, int] = {}


def _tenant(slug: str) -> int:
    """A real tenant row — the jobs table has a foreign key to it."""
    if slug not in TENANTS:
        if not db.get_tenant_by_slug(slug):
            db.create_tenant(slug, slug)
        TENANTS[slug] = db.get_tenant_by_slug(slug)["id"]
    return TENANTS[slug]


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_worker_thread_keeps_the_tenant() -> None:
    """A spawned stage job must run bound to the tenant that started it."""
    seen: dict[str, int | str] = {}

    def record():
        try:
            seen["tenant"] = db.current_tenant_id()
        except Exception as e:  # noqa: BLE001 - recorded, asserted below
            seen["tenant"] = f"raised: {e}"
        return "ok"

    tid = _tenant("jobsafe-a")
    with db.tenant_scope(tid):
        api_v2._spawn("00000001", "details", record)

    assert _wait_for(lambda: "tenant" in seen), "worker never ran"
    assert seen["tenant"] == tid, f"worker saw tenant {seen['tenant']!r}, expected {tid}"
    print("  OK  worker thread runs bound to the spawning tenant")


def test_two_tenants_do_not_share_a_job() -> None:
    """Same company number, two clients: separate job state and separate logs."""
    cn = "00000002"

    a_id, b_id = _tenant("jobsafe-a"), _tenant("jobsafe-b")
    for tid, status, log in ((a_id, "done", "tenant A directors"),
                             (b_id, "failed", "tenant B directors")):
        with db.tenant_scope(tid):
            db.job_claim(tid, cn, "details", "test")
            api_v2._job_set(cn, "details", status=status, log=log)

    with db.tenant_scope(a_id):
        a = api_v2._job_get(cn, "details")
    with db.tenant_scope(b_id):
        b = api_v2._job_get(cn, "details")

    assert a["status"] == "done", a
    assert b["status"] == "failed", b
    assert "B" not in a["log"], f"tenant A can see tenant B's log: {a['log']!r}"
    assert "A" not in b["log"], f"tenant B can see tenant A's log: {b['log']!r}"
    print("  OK  job state and logs are separate per tenant")


def test_job_route_requires_ownership() -> None:
    """The job route must resolve the company for this tenant before answering."""
    import app as flask_app

    calls: list[str] = []

    def _no_row(cn):
        calls.append(cn)
        return None

    original = api_v2._find_row
    api_v2._find_row = _no_row
    try:
        client = flask_app.app.test_client()
        with flask_app.app.test_request_context():
            response = api_v2.v2_job("00000003", "details")
        body, status = response if isinstance(response, tuple) else (response, 200)
        assert status == 404, f"expected 404 for a company this tenant does not own, got {status}"
        assert calls == ["00000003"], f"route did not resolve the row: {calls}"
    finally:
        api_v2._find_row = original
    print("  OK  job route 404s on a company the tenant does not own")


def test_build_slots_are_bounded() -> None:
    """Only one Android build runs at a time; the rest report queued, not running."""
    started = threading.Event()
    release = threading.Event()
    statuses: list[str] = []

    def blocker():
        started.set()
        release.wait(5)
        return "done"

    tid = _tenant("jobsafe-a")
    with db.tenant_scope(tid):
        api_v2._spawn("00000004", "app", blocker)
        assert started.wait(5), "first build never started"
        api_v2._spawn("00000005", "app", lambda: "second")
        assert _wait_for(
            lambda: api_v2._job_get("00000005", "app").get("status") == "queued",
        ), "second build was not queued behind the first"
        statuses.append(api_v2._job_get("00000004", "app")["status"])
        release.set()
        assert _wait_for(
            lambda: api_v2._job_get("00000005", "app").get("status") == "done",
        ), "second build never ran after the slot freed"

    assert statuses == ["running"], statuses
    print("  OK  concurrent builds queue instead of thrashing the box")


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL not set — skipping (the job registry is in Postgres).")
        return 0
    db.ensure_schema()
    tests = [
        test_worker_thread_keeps_the_tenant,
        test_two_tenants_do_not_share_a_job,
        test_job_route_requires_ownership,
        test_build_slots_are_bounded,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
