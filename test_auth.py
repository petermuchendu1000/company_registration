"""
Authentication and tenant-isolation tests for the HTTP layer.

Needs a throwaway PostgreSQL database — these create and drop tables, and
write fixture files under pipeline_output/companies/. Never point this at
production.

    createdb authtest
    DATABASE_URL="postgresql://postgres@localhost/authtest" python test_auth.py
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shutil
import sys
import threading
import time
import uuid

os.environ.setdefault("FLASK_SECRET_KEY", "test-only-secret")
os.environ.setdefault("COMPANIES_HOUSE_API_KEY", "dummy")

import db  # noqa: E402

ROOT = pathlib.Path(__file__).parent
COMPANIES = ROOT / "pipeline_output" / "companies"

_fixtures: list[pathlib.Path] = []


def _client(flask_app, email: str | None = None, password: str | None = None):
    c = flask_app.test_client()
    if email:
        r = c.post("/login", data={"email": email, "password": password})
        assert r.status_code in (200, 302), f"login failed: {r.status_code}"
    return c


def _concrete(rule) -> str:
    """Fill a rule's path parameters with a plausible value."""
    path = str(rule)
    for arg in rule.arguments:
        path = re.sub(r"<[^<>]*\b" + re.escape(arg) + r">", "13510663", path)
    return path


def _seed():
    """Two tenants, a user and a company each, with a real file on disk."""
    suffix = uuid.uuid4().hex[:8]
    a_slug, b_slug = f"acme-{suffix}", f"globex-{suffix}"
    a = db.create_tenant(a_slug)
    b = db.create_tenant(b_slug)
    a_email, b_email = f"alice-{suffix}@test", f"bob-{suffix}@test"
    db.create_user(a, a_email, "pw-a")
    db.create_user(b, b_email, "pw-b")

    a_cn, b_cn = "11111111", "22222222"
    db.add_company(a, a_cn, "Acme Co")
    db.add_company(b, b_cn, "Globex Co")
    for cn, nm in ((a_cn, "Acme Co"), (b_cn, "Globex Co")):
        d = COMPANIES / f"{cn} - {nm}" / "certificate"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cert.pdf").write_bytes(b"%PDF-1.4 fixture")
        _fixtures.append(COMPANIES / f"{cn} - {nm}")
    return {
        "a": {"tenant": a, "email": a_email, "pw": "pw-a", "cn": a_cn,
              "file": str((COMPANIES / f"{a_cn} - Acme Co" / "certificate" / "cert.pdf").resolve())},
        "b": {"tenant": b, "email": b_email, "pw": "pw-b", "cn": b_cn,
              "file": str((COMPANIES / f"{b_cn} - Globex Co" / "certificate" / "cert.pdf").resolve())},
    }


def test_every_route_denies_anonymous(flask_app, seed) -> None:
    """Deny by default: no route outside PUBLIC_ENDPOINTS answers without a session."""
    import auth
    c = flask_app.test_client()
    leaked = []
    for rule in flask_app.url_map.iter_rules():
        if rule.endpoint in auth.PUBLIC_ENDPOINTS:
            continue
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            resp = c.open(_concrete(rule), method=method)
            ok = resp.status_code == 401 or (
                resp.status_code in (301, 302)
                and "/login" in resp.headers.get("Location", "")
            )
            if not ok:
                leaked.append((method, _concrete(rule), resp.status_code))
    assert not leaked, f"routes reachable without a session: {leaked}"


def test_public_endpoints_are_reachable(flask_app, seed) -> None:
    c = flask_app.test_client()
    assert c.get("/health").status_code == 200
    assert c.get("/login").status_code == 200


def test_login_and_logout(flask_app, seed) -> None:
    a = seed["a"]
    c = flask_app.test_client()
    assert c.get("/api/v2/companies").status_code == 401
    c.post("/login", data={"email": a["email"], "password": a["pw"]})
    assert c.get("/api/v2/companies").status_code == 200
    c.get("/logout")
    assert c.get("/api/v2/companies").status_code == 401


def test_bad_credentials_rejected(flask_app, seed) -> None:
    a = seed["a"]
    c = flask_app.test_client()
    r = c.post("/login", data={"email": a["email"], "password": "wrong"})
    assert r.status_code == 401
    assert c.get("/api/v2/companies").status_code == 401


def test_company_list_is_tenant_scoped(flask_app, seed) -> None:
    def numbers(who):
        c = _client(flask_app, seed[who]["email"], seed[who]["pw"])
        payload = json.loads(c.get("/api/v2/companies").data)
        rows = payload if isinstance(payload, list) else payload.get("companies", [])
        return {r.get("cn") or r.get("company_number") for r in rows} - {None}

    assert numbers("a") == {seed["a"]["cn"]}
    assert numbers("b") == {seed["b"]["cn"]}


def test_other_tenants_company_not_readable(flask_app, seed) -> None:
    c = _client(flask_app, seed["a"]["email"], seed["a"]["pw"])
    body = c.get(f"/api/v2/company/{seed['b']['cn']}").get_data(as_text=True)
    assert "Globex Co" not in body, "another tenant's company name leaked"


def test_own_file_downloads(flask_app, seed) -> None:
    c = _client(flask_app, seed["a"]["email"], seed["a"]["pw"])
    r = c.get(f"/download?path={seed['a']['file']}")
    assert r.status_code == 200 and r.data.startswith(b"%PDF"), r.status_code


def test_cross_tenant_file_download_blocked(flask_app, seed) -> None:
    c = _client(flask_app, seed["a"]["email"], seed["a"]["pw"])
    for route in ("/download", "/api/v2/dl"):
        r = c.get(f"{route}?path={seed['b']['file']}")
        assert r.status_code == 404, f"{route} served another tenant's file ({r.status_code})"


def test_path_traversal_blocked(flask_app, seed) -> None:
    """
    Containment must survive traversal, absolute paths, and a sibling
    directory whose name merely starts with the base path — the case a
    str.startswith() check lets through.
    """
    outside = ROOT / "pipeline_output" / "SECRET.txt"
    outside.write_text("top secret")
    sibling = ROOT / "pipeline_output_backup"
    sibling.mkdir(exist_ok=True)
    (sibling / "stolen.txt").write_text("payload")
    _fixtures.extend([outside, sibling])

    c = _client(flask_app, seed["a"]["email"], seed["a"]["pw"])
    attacks = [
        str(outside.resolve()),
        str((sibling / "stolen.txt").resolve()),
        str(COMPANIES / ".." / ".." / "db.py"),
        "/etc/passwd",
        "",
    ]
    for route in ("/download", "/api/v2/dl"):
        for attack in attacks:
            code = c.get(f"{route}?path={attack}").status_code
            assert code in (400, 403, 404), f"{route} served {attack!r} ({code})"


def test_open_redirect_blocked(flask_app, seed) -> None:
    a = seed["a"]
    c = flask_app.test_client()
    r = c.post("/login?next=https://evil.test/steal",
               data={"email": a["email"], "password": a["pw"]})
    assert "evil.test" not in r.headers.get("Location", "")


def test_tenant_unbound_after_request(flask_app, seed) -> None:
    """A contextvar left set would bleed into the next request on this thread."""
    a = seed["a"]
    c = _client(flask_app, a["email"], a["pw"])
    c.get("/api/v2/companies")
    try:
        db.current_tenant_id()
    except RuntimeError:
        return
    raise AssertionError("tenant still bound after the request finished")


def test_concurrent_tenants_do_not_mix(flask_app, seed) -> None:
    """Worker threads are pooled and reused; two tenants must stay separate."""
    import requests
    from werkzeug.serving import make_server

    port = 5099
    srv = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.5)
    base = f"http://127.0.0.1:{port}"
    errors: list = []

    def session_for(who):
        s = requests.Session()
        s.post(f"{base}/login",
               data={"email": seed[who]["email"], "password": seed[who]["pw"]}, timeout=5)
        return s

    def hammer(sess, expect, n=40):
        for _ in range(n):
            try:
                payload = sess.get(f"{base}/api/v2/companies", timeout=5).json()
                rows = payload if isinstance(payload, list) else payload.get("companies", [])
                got = {r.get("cn") or r.get("company_number") for r in rows} - {None}
                if got != {expect}:
                    errors.append((expect, got))
            except Exception as exc:  # noqa: BLE001
                errors.append((expect, repr(exc)))

    try:
        sa, sb = session_for("a"), session_for("b")
        threads = [
            threading.Thread(target=hammer, args=(sa, seed["a"]["cn"])),
            threading.Thread(target=hammer, args=(sb, seed["b"]["cn"])),
            threading.Thread(target=hammer, args=(sa, seed["a"]["cn"])),
            threading.Thread(target=hammer, args=(sb, seed["b"]["cn"])),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        srv.shutdown()

    assert not errors, f"cross-tenant contamination: {errors[:5]}"


def _cleanup():
    for path in _fixtures:
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    if not (os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")):
        print("Set DATABASE_URL to a throwaway database first.")
        return 2

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    db.ensure_schema()

    import app as application
    flask_app = application.app
    flask_app.config["TESTING"] = True

    seed = _seed()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    try:
        for fn in tests:
            try:
                fn(flask_app, seed)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
            else:
                print(f"ok    {fn.__name__}")
    finally:
        _cleanup()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
