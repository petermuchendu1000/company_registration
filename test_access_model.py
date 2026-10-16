"""
Who may do what.

auth.py has carried an `operator_required` decorator, commented "For
cross-tenant views. Clients must never reach these", applied to no route at
all. `login_required` likewise. The before_request hook authenticated
everyone and then stopped: a user created with role='client' could

    POST /api/v2/company/<cn>/stage/domain     register a domain, spend money
    POST /api/v2/company/<cn>/run-all          run the whole pipeline
    POST /api/domains/register                 the older route, same effect
    GET  /api/v2/company/<cn>/job/<stage>      read tracebacks and server paths

Three roles now: client reads its own tenant's companies and downloads its
own files; operator runs the pipeline for its tenant; admin is operator plus
the cross-tenant views.

The gate is an ALLOWLIST in the before_request hook, not a deny-list and not
per-route decorators. The route table carries legacy endpoints that register
domains and drive browsers; a deny-list protects only what somebody
remembered to add. The test that matters most here is
test_every_route_is_closed_to_a_client_unless_allowlisted, which walks the
real url_map rather than a list written by hand.

    DATABASE_URL=... python test_access_model.py
"""

from __future__ import annotations

import os
import uuid

if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
    print("DATABASE_URL not set — skipping (these need a real Postgres).")
    raise SystemExit(0)

os.environ.setdefault("FLASK_SECRET_KEY", "access-model-test")
os.environ.pop("SINGLE_TENANT_SLUG", None)

import db                       # noqa: E402
import auth                     # noqa: E402
import app as flask_app         # noqa: E402

PASSWORD = "access-model-password"
CN = "40000001"
NAME = "HARBOUR COURIERS LTD"


class Actor:
    """A signed-in user of one role, with a client bound to the real app."""

    def __init__(self, role: str, tenant_id: int):
        self.role = role
        self.tenant_id = tenant_id
        self.email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
        db.create_user(tenant_id, self.email, PASSWORD, role)
        self.client = flask_app.app.test_client()
        r = self.client.post("/login", data={"email": self.email, "password": PASSWORD})
        assert r.status_code in (200, 302), f"{role} could not sign in: {r.status_code}"

    def get(self, path):
        return self.client.get(path, headers={"Accept": "application/json"})

    def post(self, path, **kw):
        return self.client.post(path, headers={"Accept": "application/json"}, **kw)


def _tenant(prefix="acl") -> int:
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return db.create_tenant(slug, slug)


# Built once: creating tenants and users is the slow part.
TENANT = _tenant()
db.add_company(TENANT, CN, NAME, "2019-04-02")
CLIENT = Actor("client", TENANT)
OPERATOR = Actor("operator", TENANT)
ADMIN = Actor("admin", TENANT)


# ── the roles exist ──────────────────────────────────────────────────────────

def test_admin_is_a_real_role() -> None:
    assert "admin" in db.VALID_ROLES, db.VALID_ROLES
    print("  OK  admin is an accepted role")


# ── the client is confined ───────────────────────────────────────────────────

def test_client_cannot_spend_money() -> None:
    """The one that matters: the domain stage buys a real domain."""
    r = CLIENT.post(f"/api/v2/company/{CN}/stage/domain")
    assert r.status_code == 403, f"a client reached the domain stage: {r.status_code}"
    print("  OK  a client cannot start the domain stage")


def test_client_cannot_run_the_pipeline() -> None:
    r = CLIENT.post(f"/api/v2/company/{CN}/run-all")
    assert r.status_code == 403, r.status_code
    print("  OK  a client cannot run the whole pipeline")


def test_client_cannot_reach_the_older_domain_route() -> None:
    """wizard.domains_register predates api_v2 and registers domains too."""
    r = CLIENT.post("/api/domains/register")
    assert r.status_code == 403, f"a client reached the legacy register route: {r.status_code}"
    print("  OK  a client cannot reach the legacy domain-registration route")


def test_client_cannot_read_a_stage_log() -> None:
    """A GET, so "read-only" is not the test. Logs carry tracebacks."""
    r = CLIENT.get(f"/api/v2/company/{CN}/job/details")
    assert r.status_code == 403, f"a client read a stage log: {r.status_code}"
    print("  OK  a client cannot read a stage log")


def test_client_cannot_open_the_operator_console() -> None:
    r = CLIENT.client.get("/app")
    assert r.status_code == 403, r.status_code
    print("  OK  a client cannot open the operator console")


def test_client_can_see_its_own_companies_and_portal() -> None:
    assert CLIENT.get("/api/v2/companies").status_code == 200
    assert CLIENT.get(f"/api/v2/company/{CN}").status_code == 200
    assert CLIENT.client.get("/portal").status_code == 200
    print("  OK  a client can read its companies and open its portal")


def test_every_role_can_ask_who_they_are() -> None:
    """
    The page needs this to decide what to offer — the admin console link,
    the signed-in name. Without it the portal rendered an empty field.
    """
    for actor in (CLIENT, OPERATOR, ADMIN):
        r = actor.get("/api/v2/me")
        assert r.status_code == 200, f"{actor.role} refused /me: {r.status_code}"
        body = r.get_json()
        assert body["email"] == actor.email, body
        assert body["role"] == actor.role, body
        assert body["tenant"], body
    print("  OK  every role can read its own identity")


def test_me_reports_only_the_caller() -> None:
    """It takes no argument, so there is no way to ask about anyone else."""
    other = OPERATOR.email
    for query in (f"?user_id=1&email={other}", f"?id=1", ""):
        body = CLIENT.get("/api/v2/me" + query).get_json()
        assert body["email"] == CLIENT.email, f"query {query!r} changed the answer: {body}"
        assert body["role"] == "client", body
    leaked = [k for k in CLIENT.get("/api/v2/me").get_json()
              if "pass" in k.lower() or "hash" in k.lower()]
    assert not leaked, f"password material in /me: {leaked}"
    print("  OK  /me answers for the caller only, and carries no password material")


def test_me_needs_a_session() -> None:
    anon = flask_app.app.test_client()
    assert anon.get("/api/v2/me",
                    headers={"Accept": "application/json"}).status_code == 401
    print("  OK  /me requires a session")


def test_root_sends_each_role_to_its_own_surface() -> None:
    c = CLIENT.client.get("/")
    o = OPERATOR.client.get("/")
    assert c.status_code == 302 and c.headers["Location"].endswith("/portal"), c.headers.get("Location")
    assert o.status_code == 302 and o.headers["Location"].endswith("/app"), o.headers.get("Location")
    print("  OK  / sends a client to the portal and an operator to the console")


# ── the operator works, and stops at the tenant line ─────────────────────────

def test_operator_keeps_its_powers() -> None:
    """The gate must not have locked out the people who run the thing."""
    for path in ("/app", f"/api/v2/company/{CN}", f"/api/v2/company/{CN}/job/details"):
        r = OPERATOR.client.get(path)
        assert r.status_code == 200, f"operator refused {path}: {r.status_code}"
    print("  OK  an operator still reaches the console, company and logs")


def test_operator_is_refused_admin_endpoints() -> None:
    """ADMIN_ENDPOINTS is empty today; prove the gate works when it is not."""
    auth.ADMIN_ENDPOINTS.add("api_v2.v2_companies")
    try:
        assert OPERATOR.get("/api/v2/companies").status_code == 403, \
            "an operator reached an admin-only endpoint"
        assert ADMIN.get("/api/v2/companies").status_code == 200, \
            "an admin was refused an admin-only endpoint"
    finally:
        auth.ADMIN_ENDPOINTS.discard("api_v2.v2_companies")
    print("  OK  an admin-only endpoint admits admin and refuses operator")


def test_admin_has_operator_powers() -> None:
    """admin is operator plus cross-tenant, not a separate narrower job."""
    assert ADMIN.client.get("/app").status_code == 200
    assert ADMIN.get(f"/api/v2/company/{CN}/job/details").status_code == 200
    print("  OK  an admin keeps every operator power")


def test_a_decorated_mutating_route_admits_operator_and_admin() -> None:
    """
    Exercises operator_required itself, not just the before_request gate.

    The gate alone would pass an admin through even if the decorator refused
    them, because only POST routes carry it — so this goes through one. Notes
    is the harmless mutating route: it writes a text field and calls nothing
    external.
    """
    body = {"notes": "set by the access-model test"}
    for actor in (OPERATOR, ADMIN):
        r = actor.post(f"/api/v2/company/{CN}/notes", json=body)
        assert r.status_code != 403, \
            f"{actor.role} was refused a mutating route it should have: {r.status_code}"
        assert r.status_code < 500, f"{actor.role} got {r.status_code}"
    assert CLIENT.post(f"/api/v2/company/{CN}/notes", json=body).status_code == 403
    print("  OK  operator and admin pass a decorated mutating route; a client does not")


# ── the allowlist holds for the whole route table ────────────────────────────

def _testable_rules():
    """Every rule a request can actually be aimed at, with a concrete path."""
    for rule in flask_app.app.url_map.iter_rules():
        if rule.endpoint in auth.PUBLIC_ENDPOINTS:
            continue
        methods = {m for m in rule.methods if m not in ("HEAD", "OPTIONS")}
        if not methods:
            continue
        try:
            path = rule.build({a: ("1" if a != "filename" else "x.css")
                               for a in rule.arguments}, append_unknown=False)[1]
        except Exception:       # noqa: BLE001 - a rule we cannot address
            continue
        yield rule.endpoint, sorted(methods)[0], path


def test_every_route_is_closed_to_a_client_unless_allowlisted() -> None:
    """
    Walks the real url_map. A route added later is covered without anyone
    remembering to extend this test.
    """
    leaked = []
    for endpoint, method, path in _testable_rules():
        if endpoint in auth.CLIENT_ENDPOINTS:
            continue
        send = CLIENT.post if method == "POST" else CLIENT.get
        status = send(path).status_code
        if status != 403:
            leaked.append(f"{method} {path} ({endpoint}) -> {status}")
    assert not leaked, "a client reached routes outside the allowlist:\n  " + "\n  ".join(leaked)
    print(f"  OK  every route outside the allowlist refuses a client "
          f"({len(list(_testable_rules()))} checked)")


def test_the_allowlist_itself_is_minimal() -> None:
    """A mutating endpoint must never be on the client allowlist."""
    writable = []
    for rule in flask_app.app.url_map.iter_rules():
        if rule.endpoint not in auth.CLIENT_ENDPOINTS:
            continue
        if {"POST", "PUT", "PATCH", "DELETE"} & set(rule.methods):
            writable.append(f"{rule.endpoint} {sorted(rule.methods)}")
    assert not writable, f"mutating endpoints are on the client allowlist: {writable}"
    print(f"  OK  all {len(auth.CLIENT_ENDPOINTS)} allowlisted endpoints are read-only")


def main() -> int:
    tests = [
        test_admin_is_a_real_role,
        test_client_cannot_spend_money,
        test_client_cannot_run_the_pipeline,
        test_client_cannot_reach_the_older_domain_route,
        test_client_cannot_read_a_stage_log,
        test_client_cannot_open_the_operator_console,
        test_client_can_see_its_own_companies_and_portal,
        test_every_role_can_ask_who_they_are,
        test_me_reports_only_the_caller,
        test_me_needs_a_session,
        test_root_sends_each_role_to_its_own_surface,
        test_operator_keeps_its_powers,
        test_operator_is_refused_admin_endpoints,
        test_admin_has_operator_powers,
        test_a_decorated_mutating_route_admits_operator_and_admin,
        test_every_route_is_closed_to_a_client_unless_allowlisted,
        test_the_allowlist_itself_is_minimal,
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
