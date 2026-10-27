#!/usr/bin/env python3
"""Read-only report on Row Level Security, ownership and grants.

    .venv/bin/python check_rls.py

Creates nothing, changes nothing, writes nothing. Every statement is a
SELECT against a catalogue view.

Why this exists. The Supabase dashboard shows RLS ENABLED on `companies`
with no policies, and says no data will be returned via the Data API.
Neither db.ensure_schema (db.py:193) nor users.ensure_schema (users.py:92)
contains an ENABLE ROW LEVEL SECURITY statement, and the same DDL run
against a plain PostgreSQL 16 leaves relrowsecurity false on every table.
So something on the Supabase side enabled it, and until that mechanism is
identified it cannot be assumed it will apply to a table created later.
Guessing either way is unsafe:

  * assume RLS will be on  -> app_users may ship with password hashes
                              readable through the project's anon key
  * assume it will be off and add ENABLE ourselves -> if our connection
                              role neither owns the table nor has
                              BYPASSRLS, the application locks ITSELF out
                              of the accounts table and nobody can log in

The second failure is the nastier one because it looks like a correct
security hardening right up until the first sign-in.

This also resolves an ambiguity in an earlier verification. deploy_check.py
reported `all_rows() round-trips: 0 companies`, which was read as "the
table is empty". A table whose rows are all filtered by RLS also returns
zero. The owner/bypass verdict below distinguishes the two without writing
a row.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

import db

INTERESTING = ("companies", "app_users", "user_audit")


def main() -> int:
    print("Row Level Security report - READ ONLY, nothing is modified")
    print(f"repo: {ROOT}")

    with db._cursor() as cur:
        cur.execute("SELECT current_user AS u, version() AS v")
        row = cur.fetchone()
        me = row["u"]
        print(f"\nconnected as : {me}")
        print(f"engine       : {row['v'].split(' on ')[0]}")

        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user")
        r = cur.fetchone()
        superuser = bool(r and r["rolsuper"])
        bypass = bool(r and r["rolbypassrls"])
        print(f"rolsuper     : {superuser}")
        print(f"rolbypassrls : {bypass}")

        print("\n=== tables in the public schema ===")
        cur.execute("""
            SELECT relname,
                   relrowsecurity      AS rls,
                   relforcerowsecurity AS force,
                   relowner::regrole::text AS owner
              FROM pg_class
             WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
             ORDER BY relname""")
        tables = [dict(x) for x in cur.fetchall()]
        if not tables:
            print("  (none)")
        for t in tables:
            owner_is_me = t["owner"] == me
            # Postgres: the table OWNER is exempt from RLS unless FORCE is
            # set; a role with BYPASSRLS is exempt regardless. Anyone else
            # sees only rows permitted by a policy - and with no policies,
            # that is no rows at all.
            if not t["rls"]:
                verdict = "RLS OFF - readable by any role holding a grant"
            elif bypass or (owner_is_me and not t["force"]):
                verdict = "RLS on, but THIS connection is exempt (owner/bypass)"
            else:
                verdict = "RLS ENFORCED on this connection - policies decide"
            mark = "  <-- " if t["relname"] in INTERESTING else "      "
            print(f"{mark}{t['relname']:16s} rls={str(t['rls']):5s} "
                  f"force={str(t['force']):5s} owner={t['owner']}")
            print(f"        {verdict}")

        print("\n=== policies ===")
        cur.execute("""SELECT tablename, policyname, cmd, roles
                         FROM pg_policies WHERE schemaname = 'public'
                        ORDER BY tablename, policyname""")
        pol = cur.fetchall()
        if not pol:
            print("  none. With RLS enabled and no policy, a non-exempt role")
            print("  sees zero rows - which is closed, not open.")
        for p in pol:
            print(f"  {p['tablename']:16s} {p['policyname']} "
                  f"cmd={p['cmd']} roles={p['roles']}")

        print("\n=== grants to the PostgREST roles (anon / authenticated) ===")
        cur.execute("""
            SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privs
              FROM information_schema.role_table_grants
             WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
             GROUP BY table_name, grantee ORDER BY table_name, grantee""")
        grants = cur.fetchall()
        if not grants:
            print("  none - those roles hold no table privileges here")
        for gr in grants:
            print(f"  {gr['table_name']:16s} {gr['grantee']:14s} {gr['privs']}")

        print("\n=== event triggers (what might be enabling RLS for us) ===")
        cur.execute("SELECT evtname, evtevent, evtenabled FROM pg_event_trigger "
                    "ORDER BY evtname")
        evts = cur.fetchall()
        if not evts:
            print("  none. If RLS is on but nothing here explains it, it was")
            print("  set by hand or by the dashboard - and a table created")
            print("  later by this application will NOT inherit it.")
        for e in evts:
            print(f"  {e['evtname']:40s} on {e['evtevent']} enabled={e['evtenabled']}")

    print("\n=== what this means for app_users ===")
    print("  If an event trigger above enables RLS on new tables, app_users")
    print("  gets it automatically and the accounts table is closed to the")
    print("  Data API. If not, app_users would ship with RLS OFF, and")
    print("  whether that exposes password hashes depends on the grants")
    print("  listed above. Decide from this output, not from assumption.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
