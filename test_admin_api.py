"""
The cross-tenant admin console.

Everything these endpoints read crosses the boundary the rest of the system
exists to hold: the data layer scopes every query to the bound tenant, and
an operator belongs to one. So two things need proving, and the second is
the one that would hurt:

  1. the numbers are right, and
  2. only an admin can see them.

The drift guard matters as much as either. The endpoints are gated twice —
`@admin_required` on the view, and the endpoint name in
`auth.ADMIN_ENDPOINTS` for the before_request hook — and the second list is
built from the url_map by `init_admin_api`, not written by hand.
`test_every_admin_endpoint_is_gated` walks the blueprint and fails if a view
is ever added that escapes either.

Alerts are asserted by constructing the condition (empty a pool, fail a job)
rather than by reading whatever the shared database happens to contain.

    DATABASE_URL=... python test_admin_api.py
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
    print("DATABASE_URL not set — skipping (these need a real Postgres).")
    raise SystemExit(0)

os.environ.setdefault("FLASK_SECRET_KEY", "admin-api-test")
os.environ.pop("SINGLE_TENANT_SLUG", None)

import db                       # noqa: E402
import auth                     # noqa: E402
import admin_api                # noqa: E402
import app as flask_app         # noqa: E402

PASSWORD = "admin-api-password"


class Actor:
    def __init__(self, role: str, tenant_id: int):
        self.role = role
        self.email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
        db.create_user(tenant_id, self.email, PASSWORD, role)
        self.client = flask_app.app.test_client()
        r = self.client.post("/login", data={"email": self.email, "password": PASSWORD})
        assert r.status_code in (200, 302), f"{role} sign-in failed: {r.status_code}"

    def get(self, path):
        return self.client.get(path, headers={"Accept": "application/json"})

    def post(self, path, **kw):
        return self.client.post(path, headers={"Accept": "application/json"}, **kw)


def _tenant(prefix: str) -> tuple[int, str]:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return db.create_tenant(slug, slug), slug


# One healthy tenant and one in trouble, built once.
HEALTHY_ID, HEALTHY = _tenant("adm-ok")
BROKEN_ID, BROKEN = _tenant("adm-bad")

db.add_company(HEALTHY_ID, "50000001", "HARBOUR COURIERS LTD", "2019-04-02")
db.update_row(HEALTHY_ID, "50000001", {
    "Company Name": "HARBOUR COURIERS LTD", "Status": "Active",
    "Address": "12 Dock Road", "DUNS Number": "221544123",
    "Domain": "harbourcouriers.online", "Assigned Email": "a@b.com",
})
db.add_company(HEALTHY_ID, "50000002", "SHELVED LTD", "2020-01-01")
db.update_row(HEALTHY_ID, "50000002", {"Company Name": "SHELVED LTD", "Archived": "Yes"})
db.import_emails(HEALTHY_ID, [f"{HEALTHY}-{i}@gmail.com" for i in range(40)])

# Broken: companies, a pool with every address taken, and a failed job.
db.add_company(BROKEN_ID, "50000003", "DOCK ELECTRICAL LTD", "2020-07-11")
db.import_emails(BROKEN_ID, [f"{BROKEN}-only@gmail.com"])
db.assign_email(BROKEN_ID, "50000003", "DOCK ELECTRICAL LTD")
db.job_claim(BROKEN_ID, "50000003", "publish", "test-worker", status="running")
db.job_update(BROKEN_ID, "50000003", "publish", status="failed",
              finished_at=dt.datetime.now(dt.timezone.utc),
              error="Missing Play service account key")

ADMIN = Actor("admin", HEALTHY_ID)
OPERATOR = Actor("operator", HEALTHY_ID)
CLIENT = Actor("client", HEALTHY_ID)

READ_PATHS = [
    "/api/v2/admin/overview",
    "/api/v2/admin/tenants",
    "/api/v2/admin/jobs",
    "/api/v2/admin/throughput",
    "/api/v2/admin/users",
]


def _all_tenants():
    """Ordering puts trouble first, so a named tenant is not on page one."""
    return ADMIN.get("/api/v2/admin/tenants?limit=500").get_json()


def _row(payload, slug):
    match = [t for t in payload["tenants"] if t["slug"] == slug]
    assert match, f"{slug} not in the returned page of {len(payload['tenants'])}"
    return match[0]


# ── the gate ─────────────────────────────────────────────────────────────────

def test_every_admin_endpoint_is_gated() -> None:
    """
    The drift guard. Every view on the blueprint must be in ADMIN_ENDPOINTS,
    which init_admin_api derives from the url_map — so a view added later is
    covered without anyone editing a second list.
    """
    on_blueprint = {r.endpoint for r in flask_app.app.url_map.iter_rules()
                    if r.endpoint.startswith("admin_api.")}
    assert on_blueprint, "the admin blueprint registered no routes"
    missing = sorted(on_blueprint - auth.ADMIN_ENDPOINTS)
    assert not missing, f"admin endpoints not in ADMIN_ENDPOINTS: {missing}"
    print(f"  OK  all {len(on_blueprint)} admin endpoints are in ADMIN_ENDPOINTS")


def test_operator_and_client_are_refused_everything() -> None:
    leaked = []
    for actor in (OPERATOR, CLIENT):
        for path in READ_PATHS + ["/admin"]:
            status = actor.get(path).status_code
            if status != 403:
                leaked.append(f"{actor.role} GET {path} -> {status}")
        for path, body in (("/api/v2/admin/users/1/role", {"role": "admin"}),
                           (f"/api/v2/admin/tenants/{HEALTHY}/emails", {"emails": ["x@y.z"]})):
            status = actor.post(path, json=body).status_code
            if status != 403:
                leaked.append(f"{actor.role} POST {path} -> {status}")
    assert not leaked, "non-admins reached admin endpoints:\n  " + "\n  ".join(leaked)
    print("  OK  every admin endpoint refuses an operator and a client")


def test_the_decorator_alone_refuses_an_operator() -> None:
    """
    Each layer on its own. With the endpoint out of ADMIN_ENDPOINTS the
    before_request gate lets an operator through, so what answers is
    @admin_required — and if it ever stopped working, the gate would hide it.
    """
    auth.ADMIN_ENDPOINTS.discard("admin_api.admin_overview")
    try:
        assert OPERATOR.get("/api/v2/admin/overview").status_code == 403, \
            "with the gate off, @admin_required did not refuse an operator"
        assert ADMIN.get("/api/v2/admin/overview").status_code == 200
    finally:
        auth.ADMIN_ENDPOINTS.add("admin_api.admin_overview")
    print("  OK  @admin_required refuses an operator with the before_request gate off")


def test_the_gate_alone_refuses_an_operator() -> None:
    """And the other layer: the gate, with the decorator stripped off."""
    view = flask_app.app.view_functions["admin_api.admin_overview"]
    flask_app.app.view_functions["admin_api.admin_overview"] = \
        getattr(view, "__wrapped__", view)
    try:
        assert OPERATOR.get("/api/v2/admin/overview").status_code == 403, \
            "with the decorator off, the before_request gate let an operator through"
    finally:
        flask_app.app.view_functions["admin_api.admin_overview"] = view
    print("  OK  the before_request gate refuses an operator with the decorator off")


def test_admin_reaches_every_read() -> None:
    for path in READ_PATHS:
        r = ADMIN.get(path)
        assert r.status_code == 200, f"admin refused {path}: {r.status_code}"
    print(f"  OK  an admin reaches all {len(READ_PATHS)} read endpoints")


# ── the numbers ──────────────────────────────────────────────────────────────

def test_tenant_summary_counts_are_right() -> None:
    body = _all_tenants()
    ok = _row(body, HEALTHY)
    assert ok["companies"] == 2, ok
    assert ok["archived"] == 1, ok
    assert ok["live_companies"] == 1, "an archived company is still in the denominator"
    # details, duns, domain, email all set on the one live company.
    assert ok["recorded_done"] == 4 and ok["recorded_possible"] == 4, ok
    assert ok["recorded_pct"] == 100, ok
    assert ok["pool_total"] == 40 and ok["pool_available"] == 40, ok
    print("  OK  per-tenant counts are right, and archived companies are excluded")


def test_a_tenant_that_crosses_no_boundary_is_still_visible() -> None:
    """The admin belongs to HEALTHY; BROKEN must still appear."""
    body = _all_tenants()
    bad = _row(body, BROKEN)
    assert bad["companies"] == 1, bad
    assert bad["pool_total"] == 1 and bad["pool_available"] == 0, bad
    assert bad["failed"] == 1, bad
    print("  OK  an admin sees a tenant they do not belong to")


def test_throughput_has_one_entry_per_day_in_order() -> None:
    body = ADMIN.get("/api/v2/admin/throughput?days=5").get_json()
    days = body["days"]
    assert len(days) == 5, len(days)
    assert [d["day"] for d in days] == sorted(d["day"] for d in days), "not in order"
    assert body.get("caveat"), "the re-run caveat is not shipped with the data"
    assert all(isinstance(d["done"], int) for d in days), days
    print("  OK  throughput returns one ordered entry per day, with its caveat")


def test_throughput_day_count_is_bounded() -> None:
    assert len(ADMIN.get("/api/v2/admin/throughput?days=9999").get_json()["days"]) == 90
    assert len(ADMIN.get("/api/v2/admin/throughput?days=0").get_json()["days"]) == 1
    assert len(ADMIN.get("/api/v2/admin/throughput?days=nonsense").get_json()["days"]) == 7
    print("  OK  the day range is clamped and a bad value falls back")


def test_job_queue_reports_the_failure() -> None:
    body = ADMIN.get("/api/v2/admin/jobs").get_json()
    mine = [f for f in body["failures"] if f["company_number"] == "50000003"]
    assert mine, f"the failed publish job is missing: {body['failures'][:3]}"
    assert mine[0]["tenant"] == BROKEN, mine[0]
    assert "service account" in (mine[0]["error"] or ""), mine[0]
    print("  OK  the job queue names the tenant, company and reason for a failure")


def test_users_never_expose_a_password_hash() -> None:
    body = ADMIN.get("/api/v2/admin/users").get_json()
    assert body["users"], "no users returned"
    leaked = [k for u in body["users"] for k in u
              if "pass" in k.lower() or "hash" in k.lower()]
    assert not leaked, f"password material in the payload: {set(leaked)}"
    assert "admin" in body["roles"], body["roles"]
    print("  OK  the user list carries no password material")


# ── alerts ───────────────────────────────────────────────────────────────────

def _alerts_for(slug):
    body = ADMIN.get("/api/v2/admin/overview").get_json()
    hits = [a for a in body["alerts"]
            if a.get("tenant") == slug or slug in (a.get("tenants") or [])]
    return hits, body


def test_an_empty_pool_is_a_critical_alert() -> None:
    alerts, _ = _alerts_for(BROKEN)
    kinds = {a["kind"] for a in alerts}
    assert "pool_empty" in kinds, f"no pool_empty alert for {BROKEN}: {alerts}"
    empty = next(a for a in alerts if a["kind"] == "pool_empty")
    assert empty["severity"] == "critical", empty
    assert BROKEN in empty["message"], empty
    print("  OK  a tenant with every mailbox taken raises a critical alert")


def test_a_tenant_with_no_login_is_flagged() -> None:
    """BROKEN has a company and no user, so nobody can see it."""
    alerts, _ = _alerts_for(BROKEN)
    assert "no_users" in {a["kind"] for a in alerts}, alerts
    print("  OK  a tenant with companies but no login is flagged")


def test_a_healthy_tenant_raises_nothing() -> None:
    alerts, _ = _alerts_for(HEALTHY)
    assert not alerts, f"a healthy tenant raised alerts: {alerts}"
    print("  OK  a healthy tenant raises no alert at all")


def test_failed_jobs_are_summarised_once() -> None:
    _, body = _alerts_for(None)
    failed = [a for a in body["alerts"] if a["kind"] == "jobs_failed"]
    assert len(failed) == 1, f"expected one failure alert, got {len(failed)}"
    assert failed[0]["count"] >= 1, failed[0]
    assert any(ch.isdigit() for ch in failed[0]["message"]), failed[0]
    print("  OK  failed jobs are summarised once, by stage")


def test_the_failure_alert_counts_them_all_not_just_the_page() -> None:
    """
    The alert used to report len(failures), which is capped by the query's
    limit — so with 200 failures it said 20.
    """
    queue = db.admin_job_queue(failed_limit=1)
    assert len(queue["failures"]) <= 1, queue["failures"]
    assert queue["failed_total"] >= len(queue["failures"]), queue["failed_total"]
    if queue["failed_total"] > 1:
        assert queue["failures_truncated"] is True, queue
        alert = next(a for a in admin_api._alerts([], queue) if a["kind"] == "jobs_failed")
        assert alert["count"] == queue["failed_total"], alert
        assert str(queue["failed_total"]) in alert["message"], alert
    print("  OK  the failure alert reports every failure, not the page it was shown")


def test_the_overview_tile_and_the_alert_agree() -> None:
    """
    They disagreed on screen: the tile said 20 failed jobs and the alert
    beside it said 31, because the tile counted the capped list.
    """
    body = ADMIN.get("/api/v2/admin/overview").get_json()
    alert = next((a for a in body["alerts"] if a["kind"] == "jobs_failed"), None)
    if alert is None:
        assert body["totals"]["failed"] == 0, body["totals"]
    else:
        assert body["totals"]["failed"] == alert["count"], \
            f"tile says {body['totals']['failed']}, alert says {alert['count']}"
    assert body["totals"]["failed"] == db.admin_job_queue()["failed_total"]
    print("  OK  the failed-jobs tile and the alert report the same number")


def test_the_tenant_table_is_bounded_and_says_so() -> None:
    """Unbounded, this rendered a 59,000-pixel page on 608 tenants."""
    body = ADMIN.get("/api/v2/admin/tenants?limit=3").get_json()
    assert len(body["tenants"]) <= 3, len(body["tenants"])
    assert body["total"] >= len(body["tenants"]), body["total"]
    if body["total"] > 3:
        assert body["truncated"] is True, body
    big = ADMIN.get("/api/v2/admin/tenants?limit=99999").get_json()
    assert len(big["tenants"]) <= 500, len(big["tenants"])
    print("  OK  the tenant list is bounded and reports the full total")


def test_tenants_in_trouble_come_first() -> None:
    """A console that opens alphabetically buries what needs reading."""
    rows = ADMIN.get("/api/v2/admin/tenants?limit=500").get_json()["tenants"]
    troubled = [i for i, t in enumerate(rows) if t["failed"]]
    calm = [i for i, t in enumerate(rows) if not t["failed"] and t["pool_available"]]
    if troubled and calm:
        assert max(troubled) < max(calm), \
            "a tenant with failures sorted below a healthy one"
    print("  OK  tenants with failures sort above healthy ones")


def test_alerts_are_grouped_not_one_per_tenant() -> None:
    """With 600 tenants an alert each is a page nobody can read."""
    body = ADMIN.get("/api/v2/admin/overview").get_json()
    alerts = body["alerts"]
    kinds = [a["kind"] for a in alerts]
    assert len(kinds) == len(set(kinds)), f"a kind appears twice: {kinds}"
    assert len(alerts) <= 10, f"{len(alerts)} alerts is a wall, not a summary"
    for a in alerts:
        assert "count" in a, a
        assert len(a.get("tenants", [])) <= 3, a
    print(f"  OK  alerts are grouped by kind ({len(alerts)} groups, not one per tenant)")


def test_overview_says_whether_the_artifact_scan_finished() -> None:
    body = ADMIN.get("/api/v2/admin/overview").get_json()
    assert "artifact_scan_complete" in body, body.keys()
    assert isinstance(body["totals"]["apps_built"], int)
    print("  OK  the overview states whether its filesystem scan was complete")


# ── writes ───────────────────────────────────────────────────────────────────

def test_importing_addresses_fills_a_pool() -> None:
    fresh = [f"{BROKEN}-new-{i}@gmail.com" for i in range(5)]
    r = ADMIN.post(f"/api/v2/admin/tenants/{BROKEN}/emails", json={"emails": fresh})
    assert r.status_code == 200, (r.status_code, r.get_json())
    body = r.get_json()
    assert body["added"] == 5, body
    assert body["pool"]["available"] == 5, body
    # ...and importing the same list again adds nothing.
    again = ADMIN.post(f"/api/v2/admin/tenants/{BROKEN}/emails",
                       json={"emails": fresh}).get_json()
    assert again["added"] == 0 and again["duplicates"] == 5, again
    print("  OK  importing addresses fills the pool and is idempotent")


def test_import_accepts_the_pasted_text_the_dialog_sends() -> None:
    """
    The console posts a textarea, not a list: newline separated, often with
    a comma or a trailing blank line in it.
    """
    pasted = (f"{BROKEN}-p1@gmail.com\n"
              f"{BROKEN}-p2@gmail.com,  {BROKEN}-p3@gmail.com\n"
              f"\n   \n")
    r = ADMIN.post(f"/api/v2/admin/tenants/{BROKEN}/emails", json={"emails": pasted})
    assert r.status_code == 200, (r.status_code, r.get_json())
    assert r.get_json()["added"] == 3, r.get_json()
    print("  OK  a pasted block of addresses imports, blank lines and commas included")


def test_importing_clears_the_alert_it_was_offered_for() -> None:
    """Otherwise the console keeps telling you to do what you just did."""
    tid, slug = _tenant("adm-bad")
    db.add_company(tid, "50000009", "EMPTY POOL LTD", "2021-01-01")
    before = {a["kind"] for a in ADMIN.get("/api/v2/admin/overview").get_json()["alerts"]
              if slug in (a.get("tenants") or []) or a.get("tenant") == slug}
    assert "pool_missing" in before, before

    ADMIN.post(f"/api/v2/admin/tenants/{slug}/emails",
               json={"emails": "\n".join(f"{slug}-{i}@gmail.com" for i in range(30))})

    after = {a["kind"] for a in ADMIN.get("/api/v2/admin/overview").get_json()["alerts"]
             if slug in (a.get("tenants") or []) or a.get("tenant") == slug}
    assert "pool_missing" not in after, f"the alert survived the import: {after}"
    print("  OK  importing addresses clears the alert that asked for them")


def test_import_refuses_junk_and_unknown_tenants() -> None:
    bad = ADMIN.post(f"/api/v2/admin/tenants/{HEALTHY}/emails",
                     json={"emails": ["not-an-address"]})
    assert bad.status_code == 400, bad.status_code
    assert "not-an-address" in str(bad.get_json()), bad.get_json()
    missing = ADMIN.post("/api/v2/admin/tenants/no-such-tenant/emails",
                         json={"emails": ["a@b.com"]})
    assert missing.status_code == 404, missing.status_code
    print("  OK  the import refuses a malformed address and an unknown tenant")


def test_changing_a_role_works_and_validates() -> None:
    victim = Actor("client", HEALTHY_ID)
    uid = next(u["id"] for u in db.admin_user_list() if u["email"] == victim.email)

    assert ADMIN.post(f"/api/v2/admin/users/{uid}/role",
                      json={"role": "wizard"}).status_code == 400
    r = ADMIN.post(f"/api/v2/admin/users/{uid}/role", json={"role": "operator"})
    assert r.status_code == 200, r.get_json()
    assert next(u["role"] for u in db.admin_user_list() if u["id"] == uid) == "operator"
    assert ADMIN.post("/api/v2/admin/users/99999999/role",
                      json={"role": "client"}).status_code == 404
    print("  OK  a role change validates the role and the user")


def test_the_last_admin_cannot_demote_themselves() -> None:
    """Otherwise the console locks out everyone, with no way back in."""
    lonely_id, _ = _tenant("adm-solo")
    solo = Actor("admin", lonely_id)
    uid = next(u["id"] for u in db.admin_user_list() if u["email"] == solo.email)

    # There are other admins in this shared database, so the guard should
    # allow it; prove the guard by checking it refuses when they are alone.
    others = [u for u in db.admin_user_list() if u["role"] == "admin" and u["id"] != uid]
    r = solo.post(f"/api/v2/admin/users/{uid}/role", json={"role": "client"})
    if others:
        assert r.status_code == 200, (r.status_code, r.get_json())
        db.set_user_role(uid, "admin")          # put it back for the next check
        for other in others:
            db.set_user_role(other["id"], "operator")
        r = solo.post(f"/api/v2/admin/users/{uid}/role", json={"role": "client"})
        assert r.status_code == 409, (r.status_code, r.get_json())
        for other in others:
            db.set_user_role(other["id"], "admin")
    else:
        assert r.status_code == 409, (r.status_code, r.get_json())
    print("  OK  the only remaining admin cannot demote themselves")


def _fake_row(slug, **over):
    row = {"slug": slug, "live_companies": 1, "users": 1,
           "pool_total": 50, "pool_available": 50, "failed": 0}
    row.update(over)
    return row


EMPTY_QUEUE = {"running": [], "queued": [], "failures": [],
               "failed_total": 0, "failed_tenants": 0, "failures_truncated": False}


def test_a_pool_warning_needs_companies_to_matter() -> None:
    """A pool filled before a tenant's first company is not a problem."""
    idle = admin_api._alerts([_fake_row("idle", live_companies=0, pool_available=1)],
                             EMPTY_QUEUE)
    assert not [a for a in idle if a["kind"] == "pool_low"], idle
    busy = admin_api._alerts([_fake_row("busy", pool_available=1)], EMPTY_QUEUE)
    assert [a for a in busy if a["kind"] == "pool_low"], busy
    print("  OK  a low pool is only flagged for a tenant that has companies")


def test_one_tenant_keeps_its_number_and_a_group_does_not_borrow_it() -> None:
    """
    "3 tenants — 1 mailbox left" would be the first tenant's count worn by
    all three.
    """
    one = admin_api._alerts([_fake_row("solo", pool_available=1)], EMPTY_QUEUE)
    msg = next(a["message"] for a in one if a["kind"] == "pool_low")
    assert "1 mailbox left" in msg, msg
    assert "mailboxes" not in msg, f"bad plural: {msg}"

    many = admin_api._alerts(
        [_fake_row("a", pool_available=1), _fake_row("b", pool_available=7)],
        EMPTY_QUEUE)
    grouped = next(a for a in many if a["kind"] == "pool_low")
    assert grouped["count"] == 2, grouped
    assert "1 mailbox left" not in grouped["message"], \
        f"a group borrowed one tenant's number: {grouped['message']}"
    print("  OK  a single tenant keeps its count; a group does not borrow it")


# ── the user list is bounded ────────────────────────────────────────────────

def test_the_user_list_is_bounded_and_reports_the_total() -> None:
    body = ADMIN.get("/api/v2/admin/users?limit=2").get_json()
    assert len(body["users"]) <= 2, len(body["users"])
    assert body["total"] >= len(body["users"]), body["total"]
    if body["total"] > 2:
        assert body["truncated"] is True, body
    big = ADMIN.get("/api/v2/admin/users?limit=99999").get_json()
    assert len(big["users"]) <= 500, len(big["users"])
    junk = ADMIN.get("/api/v2/admin/users?limit=abc&offset=-5").get_json()
    assert len(junk["users"]) <= 100 and junk["offset"] == 0, junk["offset"]
    print("  OK  the user list is bounded, paged, and reports the full total")


def test_user_paging_does_not_repeat_or_skip() -> None:
    first = ADMIN.get("/api/v2/admin/users?limit=5&offset=0").get_json()["users"]
    second = ADMIN.get("/api/v2/admin/users?limit=5&offset=5").get_json()["users"]
    ids_a = [u["id"] for u in first]
    ids_b = [u["id"] for u in second]
    assert len(set(ids_a)) == len(ids_a), "a page repeated a user"
    assert not (set(ids_a) & set(ids_b)), "two pages overlap"
    print("  OK  consecutive pages neither repeat nor overlap")


def test_admins_sort_to_the_first_page() -> None:
    users = ADMIN.get("/api/v2/admin/users?limit=500").get_json()["users"]
    roles = [u["role"] for u in users]
    if "admin" in roles and "client" in roles:
        assert roles.index("admin") < roles.index("client"), \
            "a client sorted above an admin"
    print("  OK  admins come first, so the console shows who can reach it")


# ── cleaning up test tenants ────────────────────────────────────────────────

def test_the_signature_never_matches_a_real_name() -> None:
    """
    The guard that matters. A person names a tenant acme or borough; the
    suites name one alpha-1f3598a9. Only the second shape is disposable.
    """
    pattern = db._test_slug_re()
    for safe in ("acme", "borough", "portal-demo", "default", "alpha",
                 "alpha-production", "beta-client", "e2e", "acme-holdings",
                 "mailpool-a", "jobtest-b", "alpha-1f3598a", "alpha-1f3598a9x",
                 "ALPHA-1f3598a9", "alpha-1F3598A9", "alpha-zzzzzzzz"):
        assert not pattern.match(safe), f"the signature matched a real name: {safe!r}"
    for debris in ("alpha-1f3598a9", "beta-00000000", "e2efail-2475afca",
                   "adm-bad-125bb5a2"):
        assert pattern.match(debris), f"the signature missed test debris: {debris!r}"
    print("  OK  the delete signature matches test debris and no plausible real name")


def test_a_short_prefix_is_refused() -> None:
    """A two-letter prefix would match far too much."""
    for bad in ("a", "ab", "A-B", "x y"):
        try:
            db._test_slug_re((bad,))
        except ValueError:
            continue
        raise AssertionError(f"accepted an unsafe prefix: {bad!r}")
    print("  OK  an unsafe test prefix is refused")


def test_delete_refuses_a_tenant_without_the_signature() -> None:
    """Passing a real tenant's id must delete nothing, not trim the list."""
    real_id = db.create_tenant(f"realco-{uuid.uuid4().hex[:6]}", "Real Co")
    try:
        try:
            db.delete_tenants([real_id], confirm=True)
        except ValueError as e:
            assert "signature" in str(e), e
        else:
            raise AssertionError("a tenant without the signature was deleted")
        assert db.get_tenant(real_id) is not None, "the real tenant is gone"
    finally:
        # This fixture cannot carry the signature — that is the point of it —
        # so the guarded delete will not take it, and leaving it behind would
        # add a tenant per run to the very debris this cleanup exists to
        # remove. Removed directly, which only a test may do.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {db.TENANTS_TABLE} WHERE id = %s", (real_id,))
    print("  OK  deleting refuses any tenant that lacks the signature")


def test_dry_run_deletes_nothing() -> None:
    doomed = db.create_tenant(db.test_tenant_slug("cpaths"), "throwaway")
    out = db.delete_tenants([doomed], confirm=False)
    assert out["deleted"] == 0 and out.get("dry_run") is True, out
    assert db.get_tenant(doomed) is not None, "a dry run deleted it anyway"
    db.delete_tenants([doomed], confirm=True)
    print("  OK  without confirm the delete reports what it would do and stops")


def test_deleting_a_test_tenant_takes_its_rows_with_it() -> None:
    victim = db.create_tenant(db.test_tenant_slug("cpaths"), "throwaway")
    db.add_company(victim, "60000001", "THROWAWAY LTD", "2021-01-01")
    db.create_user(victim, f"gone-{uuid.uuid4().hex[:8]}@example.com", PASSWORD, "client")
    db.import_emails(victim, [f"gone-{uuid.uuid4().hex[:8]}@gmail.com"])
    db.job_claim(victim, "60000001", "details", "test-worker")

    found = [t for t in db.find_test_tenants() if t["id"] == victim]
    assert found, "find_test_tenants did not see a freshly created test tenant"
    assert found[0]["companies"] == 1 and found[0]["users"] == 1, found[0]

    out = db.delete_tenants([victim], confirm=True)
    assert out["deleted"] == 1, out
    assert db.get_tenant(victim) is None
    with db.tenant_scope(victim):
        pass  # binding a dead tenant is harmless; the rows are what matter
    assert not [t for t in db.find_test_tenants() if t["id"] == victim]
    print("  OK  deleting a test tenant removes it and everything cascading off it")


def test_file_cleanup_stays_inside_the_companies_root() -> None:
    """A slug is a path segment; it must not be able to point outside."""
    import company_paths
    root = company_paths.companies_root()
    sentinel = root.parent / "do-not-delete.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep", encoding="utf-8")
    try:
        removed = db.delete_test_tenant_files(
            ["../do-not-delete.txt", "acme", "alpha-deadbeef"])
        assert removed == [] or removed == ["alpha-deadbeef"], removed
        assert sentinel.exists(), "file cleanup escaped the companies root"
    finally:
        sentinel.unlink(missing_ok=True)
    print("  OK  file cleanup refuses traversal and non-signature names")


def main() -> int:
    tests = [
        test_every_admin_endpoint_is_gated,
        test_operator_and_client_are_refused_everything,
        test_the_decorator_alone_refuses_an_operator,
        test_the_gate_alone_refuses_an_operator,
        test_admin_reaches_every_read,
        test_tenant_summary_counts_are_right,
        test_a_tenant_that_crosses_no_boundary_is_still_visible,
        test_throughput_has_one_entry_per_day_in_order,
        test_throughput_day_count_is_bounded,
        test_job_queue_reports_the_failure,
        test_users_never_expose_a_password_hash,
        test_an_empty_pool_is_a_critical_alert,
        test_a_tenant_with_no_login_is_flagged,
        test_a_healthy_tenant_raises_nothing,
        test_failed_jobs_are_summarised_once,
        test_the_failure_alert_counts_them_all_not_just_the_page,
        test_the_overview_tile_and_the_alert_agree,
        test_the_tenant_table_is_bounded_and_says_so,
        test_tenants_in_trouble_come_first,
        test_alerts_are_grouped_not_one_per_tenant,
        test_overview_says_whether_the_artifact_scan_finished,
        test_importing_addresses_fills_a_pool,
        test_import_accepts_the_pasted_text_the_dialog_sends,
        test_importing_clears_the_alert_it_was_offered_for,
        test_import_refuses_junk_and_unknown_tenants,
        test_changing_a_role_works_and_validates,
        test_the_last_admin_cannot_demote_themselves,
        test_a_pool_warning_needs_companies_to_matter,
        test_one_tenant_keeps_its_number_and_a_group_does_not_borrow_it,
        test_the_user_list_is_bounded_and_reports_the_total,
        test_user_paging_does_not_repeat_or_skip,
        test_admins_sort_to_the_first_page,
        test_the_signature_never_matches_a_real_name,
        test_a_short_prefix_is_refused,
        test_delete_refuses_a_tenant_without_the_signature,
        test_dry_run_deletes_nothing,
        test_deleting_a_test_tenant_takes_its_rows_with_it,
        test_file_cleanup_stays_inside_the_companies_root,
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
