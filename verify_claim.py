#!/usr/bin/env python3
"""Verify the atomic domain-purchase claim against the LIVE database.

    .venv/bin/python verify_claim.py --yes [--workers 24]

THIS WRITES TO PRODUCTION. It creates one row with the sentinel company
number 00000000, exercises db.claim_domain_purchase() against it, and
deletes the row again. It refuses to run without --yes.

Why it exists: claim_domain_purchase (db.py:377) is the single mechanism
preventing this system paying twice for the same domain. It is an
  INSERT ... ON CONFLICT (company_number) DO UPDATE ... WHERE
compare-and-swap, so exactly one of N concurrent callers may come away with
the claim. That was mutation-tested against PostgreSQL 16.15. Supabase runs
17.11. ON CONFLICT semantics have been stable since 9.5 and nothing in the
17 notes touches them, but "stable since 9.5" is reasoning and this is a
money guarantee, so it gets observed on the engine that will actually run.

The sentinel 00000000 is not a valid UK company number - Companies House
never issues it - and db._excel_records already skips it explicitly
(db.py:581), so it cannot arrive through an import either.

Safety interlocks:
  * --yes is required.
  * It aborts if the sentinel row already exists, rather than adopting a
    row it did not create; the abort message gives the exact cleanup SQL.
  * Cleanup runs in a finally block and is then VERIFIED, not assumed.
  * It touches no company number other than the sentinel.

Exit code is the number of failed checks.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

import db

SENTINEL = "00000000"
failures: list[str] = []


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)
    return ok


class ClaimAudit(logging.Handler):
    """Counts what the money audit channel actually recorded.

    The claim's return value and its audit trail are two different things,
    and a system that refuses correctly but logs nothing is still broken -
    the log is what tells an operator a domain may already have been
    charged for.
    """

    def __init__(self):
        super().__init__()
        self.taken = 0
        self.refused = 0
        # NOT self.lock. logging.Handler owns that name: Handler.handle()
        # acquires self.lock (an RLock) and then calls emit(). Rebinding it
        # to a plain non-reentrant Lock makes emit() block forever trying to
        # re-acquire a lock its own caller already holds - a single-threaded
        # self-deadlock that looks exactly like a hung database query.
        self._count_lock = threading.Lock()

    def emit(self, record):
        msg = record.getMessage()
        with self._count_lock:
            if "claim TAKEN" in msg:
                self.taken += 1
            elif "claim REFUSED" in msg:
                self.refused += 1


def sentinel_state(cur):
    cur.execute(
        f"SELECT company_number, row_no, domain_status FROM {db.TABLE} "
        f"WHERE company_number = %s", (SENTINEL,))
    return cur.fetchone()


def cleanup() -> None:
    print()
    print("=== cleanup ===")
    with db._cursor() as cur:
        cur.execute(f"DELETE FROM {db.TABLE} WHERE company_number = %s",
                    (SENTINEL,))
        deleted = cur.rowcount
    db._invalidate()
    print(f"  deleted {deleted} sentinel row(s)")
    # Verified, not assumed: a cleanup that silently failed would leave a
    # permanent fake company in a production table.
    with db._cursor() as cur:
        left = sentinel_state(cur)
    check("cleanup removed the sentinel row", left, None)
    with db._cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {db.TABLE}")
        print(f"  rows remaining in '{db.TABLE}': {cur.fetchone()['n']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="required: confirms this may write to production")
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()

    if not args.yes:
        print(__doc__)
        print("Refusing to run without --yes.")
        return 1

    print("=== preflight ===")
    with db._cursor() as cur:
        cur.execute("SELECT current_database() AS d, version() AS v")
        row = cur.fetchone()
        print(f"  database: {row['d']}")
        print(f"  engine  : {row['v'].split(' on ')[0]}")
        cur.execute(f"SELECT count(*) AS n FROM {db.TABLE}")
        total = cur.fetchone()["n"]
        print(f"  rows in '{db.TABLE}': {total}")
        existing = sentinel_state(cur)

    if existing is not None:
        print()
        print(f"  ABORT: a row for sentinel {SENTINEL} already exists:")
        print(f"         {dict(existing)}")
        print( "         This script did not create it, so it will not")
        print( "         delete it. Remove it yourself and re-run:")
        print(f"           DELETE FROM {db.TABLE} "
              f"WHERE company_number = '{SENTINEL}';")
        return 1
    check("sentinel row absent before we start", existing, None)

    audit = ClaimAudit()
    logging.getLogger("money").addHandler(audit)
    logging.getLogger("money").setLevel(logging.INFO)

    try:
        # ── C1: a claim on a company with no row must INSERT and win ──────
        print()
        print("=== C1: first claim creates the row and wins ===")
        check("first claim granted", db.claim_domain_purchase(SENTINEL), True)
        check("claim now held", db.domain_claim_held(SENTINEL), True)
        with db._cursor() as cur:
            st = sentinel_state(cur)
        check("domain_status is the claim marker",
              st and st["domain_status"], db.DOMAIN_CLAIM)
        check("row_no was assigned", bool(st and st["row_no"]), True)

        # ── C2: a second claim while one is outstanding must be REFUSED ───
        print()
        print("=== C2: a second claim while one is outstanding is refused ===")
        check("second claim refused", db.claim_domain_purchase(SENTINEL), False)
        check("claim still held by the first", db.domain_claim_held(SENTINEL), True)

        # ── C3: once resolved, a new claim may be taken ───────────────────
        print()
        print("=== C3: after the claim resolves, a new one may be taken ===")
        db.update_row(SENTINEL, {"Domain Status": "live"})
        check("claim no longer outstanding", db.domain_claim_held(SENTINEL), False)
        check("new claim granted", db.claim_domain_purchase(SENTINEL), True)

        # ── C4: THE one that matters ──────────────────────────────────────
        print()
        print(f"=== C4: {args.workers} concurrent claimants, exactly one may win ===")
        db.update_row(SENTINEL, {"Domain Status": "live"})
        check("reset: no claim outstanding", db.domain_claim_held(SENTINEL), False)

        audit.taken = audit.refused = 0
        n = args.workers
        barrier = threading.Barrier(n)
        results: list[bool] = []
        rlock = threading.Lock()

        def worker():
            barrier.wait()          # all threads fire as close to together
            got = db.claim_domain_purchase(SENTINEL)   # as the GIL allows
            with rlock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = sum(1 for r in results if r)
        print(f"    {len(results)} claimants ran, {winners} won, "
              f"{len(results) - winners} were refused")
        check(f"exactly ONE of {n} concurrent claimants won", winners, 1)
        check("all claimants returned a verdict", len(results), n)
        check("claim is held afterwards", db.domain_claim_held(SENTINEL), True)

        # The audit trail must agree with the return values.
        print(f"    money log recorded: TAKEN={audit.taken} "
              f"REFUSED={audit.refused}")
        check("audit log recorded exactly one TAKEN", audit.taken, 1)
        check("audit log recorded a REFUSED for every loser",
              audit.refused, n - 1)

        # ── C5: a company number that normalises to nothing is refused ────
        print()
        print("=== C5: an empty company number is refused, not inserted ===")
        check("empty cn refused", db.claim_domain_purchase(""), False)
        check("whitespace cn refused", db.claim_domain_purchase("   "), False)
        with db._cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {db.TABLE} "
                        f"WHERE company_number IN ('', '   ')")
            check("no junk row created", cur.fetchone()["n"], 0)

    finally:
        logging.getLogger("money").removeHandler(audit)
        try:
            cleanup()
        except Exception as e:
            print(f"  CLEANUP FAILED: {type(e).__name__}: {e}")
            print(f"  Remove it by hand:  DELETE FROM {db.TABLE} "
                  f"WHERE company_number = '{SENTINEL}';")
            failures.append("cleanup")

    print()
    print(f"RESULT: {len(failures)} failure(s) {failures or ''}")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
