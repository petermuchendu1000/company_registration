"""
The durable job registry.

Needs a real Postgres — the behaviour under test is the atomicity of the
claim and the reclamation of rows left by a dead process, neither of which a
fake would exercise.

    DATABASE_URL=postgresql://... python test_jobs_store.py
"""

from __future__ import annotations

import os
import threading
import time

import db


TENANT_A = None
TENANT_B = None


def _setup() -> None:
    global TENANT_A, TENANT_B
    db.ensure_schema()
    for slug in ("jobtest-a", "jobtest-b"):
        if not db.get_tenant_by_slug(slug):
            db.create_tenant(slug, slug)
    TENANT_A = db.get_tenant_by_slug("jobtest-a")["id"]
    TENANT_B = db.get_tenant_by_slug("jobtest-b")["id"]
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {db.JOBS_TABLE} WHERE tenant_id IN (%s, %s)",
                    (TENANT_A, TENANT_B))
        conn.commit()


def test_claim_is_exclusive() -> None:
    """A second claim on a running stage must lose."""
    assert db.job_claim(TENANT_A, "10000001", "app", "worker-1") is True
    assert db.job_claim(TENANT_A, "10000001", "app", "worker-2") is False
    db.job_update(TENANT_A, "10000001", "app", status="done")
    assert db.job_claim(TENANT_A, "10000001", "app", "worker-2") is True
    print("  OK  claim is exclusive while active, free once finished")


def test_concurrent_claims_elect_one_winner() -> None:
    """
    The real race: eight threads claim the same stage at once.

    A read-then-write guard lets several through; this must let exactly one.
    """
    wins: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def contend(n: int) -> None:
        barrier.wait()
        won = db.job_claim(TENANT_A, "10000002", "details", f"worker-{n}")
        with lock:
            wins.append(won)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(wins) == 1, f"expected exactly one winner, got {sum(wins)}"
    print("  OK  eight simultaneous claims elect exactly one winner")


def test_tenants_do_not_share_jobs() -> None:
    """Same company number, two clients: separate rows, separate logs."""
    cn = "10000003"
    db.job_claim(TENANT_A, cn, "duns", "worker-1")
    db.job_update(TENANT_A, cn, "duns", status="done", log="tenant A directors")
    db.job_claim(TENANT_B, cn, "duns", "worker-1")
    db.job_update(TENANT_B, cn, "duns", status="failed", log="tenant B directors")

    a = db.job_get(TENANT_A, cn, "duns")
    b = db.job_get(TENANT_B, cn, "duns")
    assert a["status"] == "done" and b["status"] == "failed", (a, b)
    assert "B" not in a["log"], f"tenant A sees tenant B's log: {a['log']!r}"
    assert "A" not in b["log"], f"tenant B sees tenant A's log: {b['log']!r}"

    assert cn not in db.active_jobs(TENANT_A), "finished job still reported active"
    print("  OK  job rows and logs are per tenant")


def test_restart_reclaims_interrupted_jobs() -> None:
    """
    A job left running by a dead process must be failed, not left blocking.

    This is the whole reason the registry is durable: in memory the job
    vanished on restart and the client was never told the build died.
    """
    cn = "10000004"
    assert db.job_claim(TENANT_A, cn, "app", "worker-that-died") is True
    assert db.job_claim(TENANT_A, cn, "app", "worker-new") is False, \
        "a stale row should still block before reclamation"

    reclaimed = db.reclaim_stale_jobs("worker-new")
    assert reclaimed >= 1, f"nothing reclaimed (got {reclaimed})"

    job = db.job_get(TENANT_A, cn, "app")
    assert job["status"] == "failed", job
    assert "Interrupted" in (job["error"] or ""), job["error"]
    assert db.job_claim(TENANT_A, cn, "app", "worker-new") is True, \
        "stage still blocked after reclamation"
    print("  OK  interrupted jobs are failed and the stage frees up")


def test_reclaim_spares_this_worker() -> None:
    """Reclaiming must not kill work this process is still doing."""
    cn = "10000005"
    assert db.job_claim(TENANT_A, cn, "domain", "worker-live") is True
    db.reclaim_stale_jobs("worker-live")
    assert db.job_get(TENANT_A, cn, "domain")["status"] == "running", \
        "reclaim killed a job belonging to the calling worker"
    print("  OK  reclaim leaves this worker's own jobs alone")


def test_active_jobs_is_one_query_shaped_for_the_list() -> None:
    cn1, cn2 = "10000006", "10000007"
    db.job_claim(TENANT_A, cn1, "details", "w")
    db.job_claim(TENANT_A, cn1, "domain", "w")
    db.job_claim(TENANT_A, cn2, "app", "w", status="queued")
    active = db.active_jobs(TENANT_A)
    assert set(active.get(cn1, [])) == {"details", "domain"}, active
    assert active.get(cn2) == ["app"], active
    print("  OK  active_jobs groups running and queued stages by company")


def test_unknown_column_is_rejected() -> None:
    db.job_claim(TENANT_A, "10000008", "email", "w")
    try:
        db.job_update(TENANT_A, "10000008", "email", stattus="done")
    except ValueError as e:
        assert "stattus" in str(e), e
        print("  OK  a misspelt field raises instead of silently doing nothing")
        return
    raise AssertionError("expected ValueError for an unknown column")


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL not set — skipping (these need a real Postgres).")
        return 0
    _setup()
    tests = [
        test_claim_is_exclusive,
        test_concurrent_claims_elect_one_winner,
        test_tenants_do_not_share_jobs,
        test_restart_reclaims_interrupted_jobs,
        test_reclaim_spares_this_worker,
        test_active_jobs_is_one_query_shaped_for_the_list,
        test_unknown_column_is_rejected,
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
