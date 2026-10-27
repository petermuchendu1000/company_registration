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
        # evtenabled is a CHARACTER, and in many terminal fonts the letter O
        # (ascii 79, meaning enabled/origin) is indistinguishable from the
        # digit 0. Whether ensure_rls is on decides whether app_users ships
        # with password hashes exposed, so the ascii code is printed and the
        # value decoded rather than left to be read off a screen.
        # Established empirically: 'O'=79 enabled, 'D'=68 disabled.
        MEANING = {"O": "ENABLED (origin)", "D": "disabled",
                   "R": "enabled (replica only)", "A": "ENABLED (always)"}
        cur.execute("""SELECT evtname, evtevent, evtenabled::text AS state,
                              ascii(evtenabled::text) AS code
                         FROM pg_event_trigger ORDER BY evtname""")
        evts = cur.fetchall()
        if not evts:
            print("  none. If RLS is on but nothing here explains it, it was")
            print("  set by hand or by the dashboard - and a table created")
            print("  later by this application will NOT inherit it.")
        ensure_rls_on = False
        for e in evts:
            label = MEANING.get(e["state"], f"UNKNOWN({e['state']!r})")
            if e["evtname"] == "ensure_rls":
                ensure_rls_on = e["state"] in ("O", "A")
            print(f"  {e['evtname']:28s} {e['evtevent']:18s} "
                  f"ascii={e['code']:<4d} {label}")

        print("\n=== default privileges (what a NEW table inherits) ===")
        # This is the other half of the question. If anon/authenticated are
        # granted privileges on new tables by default, then app_users gets
        # them the moment it is created, and RLS is the only thing between
        # a password hash and anyone holding the project's anon key.
        cur.execute("""
            SELECT defaclrole::regrole::text AS granter,
                   defaclobjtype AS objtype,
                   defaclacl::text AS acl
              FROM pg_default_acl
             WHERE defaclnamespace = 'public'::regnamespace
             ORDER BY granter""")
        dacl = cur.fetchall()
        if not dacl:
            print("  none - new tables are granted to nobody automatically")
        for d in dacl:
            kind = {"r": "tables", "S": "sequences", "f": "functions",
                    "T": "types", "n": "schemas"}.get(d["objtype"], d["objtype"])
            print(f"  by {d['granter']:16s} on {kind:10s} {d['acl']}")
        inherits_anon = any("anon" in (d["acl"] or "") and d["objtype"] == "r"
                            for d in dacl)

        print("\n=== VERDICT for app_users, if it were created now ===")
        print(f"  ensure_rls trigger enabled : {ensure_rls_on}")
        print(f"  new tables granted to anon : {inherits_anon}")
        if ensure_rls_on:
            print("  -> RLS would be enabled automatically; with no policy the")
            print("     Data API returns nothing. Closed.")
        elif inherits_anon:
            print("  -> *** RLS would be OFF and anon would hold privileges.")
            print("     Password hashes would be reachable with the anon key.")
            print("     Do NOT start the service until this is addressed. ***")
        else:
            print("  -> RLS off, but anon holds no default privileges, so the")
            print("     Data API has no route in. Verify the grants section.")

    print("\n=== what this means for app_users ===")
    print("  If an event trigger above enables RLS on new tables, app_users")
    print("  gets it automatically and the accounts table is closed to the")
    print("  Data API. If not, app_users would ship with RLS OFF, and")
    print("  whether that exposes password hashes depends on the grants")
    print("  listed above. Decide from this output, not from assumption.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
