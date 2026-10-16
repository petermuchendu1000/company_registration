"""
Session authentication, fail-closed route protection, and per-request tenant
binding.

The enforcement model is deny-by-default. A single `before_request` hook guards
every route on the app and on every blueprint; only endpoints named in
PUBLIC_ENDPOINTS are reachable without a session. A route added later is
therefore protected the moment it exists — nobody has to remember a decorator.

On an authenticated request the signed-in user is placed on `flask.g.user` and
their tenant is bound with db.set_current_tenant(), so the data layer scopes
every query automatically. The binding is cleared on teardown: WSGI servers
reuse worker threads, and a contextvar left set would carry one client's tenant
into the next request handled by that thread.

Wire it up once, after the blueprints are registered:

    from auth import init_auth
    init_auth(app)

Set FLASK_SECRET_KEY in the environment. Without it a random key is generated
at boot, which is secure but logs everyone out on restart.
Set SESSION_COOKIE_SECURE=1 once the app is served over HTTPS.
"""
from __future__ import annotations

import functools
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from flask import (
    Blueprint, abort, g, jsonify, redirect, render_template_string,
    request, session, url_for,
)

import db

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent

# Endpoints reachable without a session. Everything else requires one.
# Keep this list short and justify every entry.
PUBLIC_ENDPOINTS: set[str] = {
    "auth.login",        # the login form and its POST
    "auth.logout",       # clearing a session needs no session
    "api_v2.v2_health",  # uptime probe; returns no data
}

# Endpoints a signed-in client may reach. Everything else needs operator or
# admin.
#
# An allowlist, not a deny-list, for the same reason PUBLIC_ENDPOINTS is one.
# The route table carries endpoints that register domains, drive a browser and
# upload to Play — wizard.domains_register and the duns_stealth_* family among
# them — and a deny-list only protects what somebody remembered to put on it.
# With an allowlist a route added tomorrow is operator-only the moment it
# exists.
#
# Read-only is not the test: a client must also not read a stage log, which is
# a GET. Logs carry tracebacks, server paths and third-party error text.
CLIENT_ENDPOINTS: set[str] = {
    "index",                   # "/" — redirects each role to its own surface
    "static",                  # stylesheet and script for the portal
    "api_v2.portal_page",      # the client's own page
    "api_v2.v2_me",            # who is signed in; takes no argument
    "api_v2.v2_companies",     # their companies, scoped by tenant already
    "api_v2.v2_company",       # one company's progress
    "api_v2.v2_download",      # their certificate, ID and app bundle
}

# Cross-tenant endpoints. An operator must not reach these: they are scoped to
# one tenant by design, and these read every tenant's data.
ADMIN_ENDPOINTS: set[str] = set()

SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))

# Login throttle: attempts per IP per window.
_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))
_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _client_ip() -> str:
    # X-Forwarded-For is set by our own nginx; the left-most entry is the client.
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


def _throttled(ip: str) -> bool:
    now = time.time()
    with _attempts_lock:
        hits = [t for t in _attempts.get(ip, []) if now - t < _WINDOW_SECONDS]
        _attempts[ip] = hits
        return len(hits) >= _MAX_ATTEMPTS


def _record_attempt(ip: str) -> None:
    now = time.time()
    with _attempts_lock:
        _attempts.setdefault(ip, []).append(now)
        if len(_attempts) > 10_000:  # bound the dict on a busy box
            for key in [k for k, v in _attempts.items()
                        if not any(now - t < _WINDOW_SECONDS for t in v)]:
                _attempts.pop(key, None)


def _clear_attempts(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


# ── Current user ────────────────────────────────────────────────────────────
def current_user() -> dict | None:
    return getattr(g, "user", None)


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _deny(status: int = 401):
    if _wants_json():
        return jsonify({"error": "authentication required"}), status
    return redirect(url_for("auth.login", next=request.full_path))


def _forbid(endpoint: str, role: str):
    """
    Refuse a signed-in user who lacks the role. 403, never 401: they are
    authenticated, so sending them back to the login form would loop.
    """
    logger.warning("role %r refused %s", role, endpoint)
    if _wants_json():
        return jsonify({
            "error": "not permitted for this account",
            "role": role,
        }), 403
    return (
        "<h1>Not permitted</h1>"
        "<p>This page is not available to your account. "
        "<a href=\"/\">Go back</a></p>"
    ), 403


# ── Decorators (the before_request hook already covers every route; these are
#    for routes needing something stricter) ──────────────────────────────────
def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return _deny()
        return fn(*args, **kwargs)
    return wrapper


def operator_required(fn):
    """
    Anything that runs the pipeline or changes a company.

    An admin passes: admin is operator plus the cross-tenant views, not a
    different job. The before_request gate already refuses a client every
    endpoint outside CLIENT_ENDPOINTS; this is the belt to that braces, for a
    route whose name somebody adds to the allowlist by mistake.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny()
        role = str(user.get("role") or "client")
        if role not in ("operator", "admin"):
            return _forbid(request.endpoint or fn.__name__, role)
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """
    For the cross-tenant views: every tenant's companies, the shared job
    queue, mailbox pools, user administration. An operator is scoped to one
    tenant and must not reach these.

    Add the endpoint name to ADMIN_ENDPOINTS as well, so the before_request
    gate refuses it even if this decorator is ever dropped.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny()
        role = str(user.get("role") or "client")
        if role != "admin":
            return _forbid(request.endpoint or fn.__name__, role)
        return fn(*args, **kwargs)
    return wrapper


# ── Tenant-scoped file access ───────────────────────────────────────────────
def tenant_company_file(raw_path: str) -> Path | None:
    """
    Resolve a requested download path, and return it only if it is a real file
    inside pipeline_output/companies/ for a company the signed-in tenant owns.

    Returns None otherwise — callers should 403/404 on that.

    Containment is checked with Path.is_relative_to after resolve(), not with
    str.startswith: a plain prefix test also matches a sibling directory such
    as pipeline_output_backup, and does not follow symlinks out of the tree.
    """
    if not raw_path:
        return None
    import company_paths

    try:
        base = company_paths.tenant_root().resolve()
    except OSError:
        return None
    try:
        target = Path(raw_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not target.is_relative_to(base) or not target.is_file():
        return None

    parts = target.relative_to(base).parts
    if not parts:
        return None
    # Company folders are "{cn}" or "{cn} - {name}".
    company_number = parts[0].split(" - ", 1)[0].strip()
    if not company_number.isdigit():
        return None

    # Holding a row for the number is necessary but no longer sufficient:
    # the file must also sit in this tenant's own subtree (enforced above)
    # and carry this tenant's ownership stamp. Two tenants may both hold
    # company 20000001 — the primary key is (tenant_id, company_number) —
    # and the row check alone served one tenant's files to the other.
    try:
        if db.find_row(db.current_tenant_id(), company_number) is None:
            return None
        if not company_paths.belongs_to(target):
            return None
    except Exception as exc:  # noqa: BLE001 - never leak a file on a lookup error
        logger.warning("tenant_company_file ownership check failed: %s", exc)
        return None
    return target


# ── Routes ──────────────────────────────────────────────────────────────────
auth_bp = Blueprint("auth", __name__)

_LOGIN_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
  /* Same tokens as static/app.html. The dashboard has no light mode, so
     following prefers-color-scheme here meant signing in on a white page and
     landing in a black one. */
  :root { color-scheme: dark; --bg:#0d1117; --card:#161b22; --fg:#e6edf3;
          --muted:#8b949e; --line:#30363d; --accent:#2f81f7; --err:#f85149;
          --field:#0d1117; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:var(--bg); color:var(--fg); padding:16px;
         font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:28px; width:100%; max-width:360px; }
  h1 { margin:0 0 4px; font-size:19px; }
  p.sub { margin:0 0 20px; color:var(--muted); font-size:13px; }
  label { display:block; font-size:13px; margin:14px 0 5px; color:var(--muted); }
  input { width:100%; padding:9px 11px; border:1px solid var(--line); border-radius:7px;
          background:var(--field); color:var(--fg); font-size:15px; }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; border-color:transparent; }
  button { width:100%; margin-top:20px; padding:10px; border:0; border-radius:7px;
           background:var(--accent); color:#fff; font-size:15px; font-weight:500; cursor:pointer; }
  button:hover { filter:brightness(1.08); }
  .err { margin:14px 0 0; padding:9px 11px; border-radius:7px; font-size:13px;
         color:var(--err); border:1px solid rgba(248,81,73,.4);
         background:rgba(248,81,73,.08); }
</style></head>
<body>
  <form class="card" method="post">
    <h1>Sign in</h1>
    <p class="sub">Company App Factory</p>
    <label for="email">Email</label>
    <input id="email" name="email" type="email" autocomplete="username" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
  </form>
</body></html>
"""


def _safe_next(raw: str | None) -> str:
    """Only ever redirect to a path on this site, never to another origin."""
    if not raw:
        return "/"
    if raw.startswith("//") or "://" in raw:
        return "/"
    return raw if raw.startswith("/") else "/"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = _client_ip()
        if _throttled(ip):
            logger.warning("login throttled for %s", ip)
            return render_template_string(
                _LOGIN_HTML, error="Too many attempts. Try again shortly.",
            ), 429

        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user = None
        try:
            user = db.verify_user(email, password)
        except Exception as exc:  # noqa: BLE001
            logger.error("login lookup failed: %s", exc)
            error = "Sign-in is temporarily unavailable."

        if user:
            _clear_attempts(ip)
            session.clear()           # new session id on login
            session.permanent = True
            session["user_id"] = user["id"]
            logger.info("login ok: user=%s tenant=%s", user["id"], user["tenant_id"])
            return redirect(_safe_next(request.args.get("next")))

        if error is None:
            _record_attempt(ip)
            logger.info("login failed for %r from %s", email, ip)
            # Deliberately vague: don't reveal whether the email exists.
            error = "Incorrect email or password."

    return render_template_string(_LOGIN_HTML, error=error), (401 if error else 200)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ── Wiring ──────────────────────────────────────────────────────────────────
_audited = False


def _audit_once(app) -> None:
    """
    Report the protected/public split once, on the first request.

    init_auth() runs right after the blueprints are registered, which is before
    any @app.route further down the main module has executed, so counting at
    that point understates the route table. Enforcement never depends on this —
    the guard below consults PUBLIC_ENDPOINTS per request — but the number in
    the log should be the real one.
    """
    global _audited
    if _audited:
        return
    _audited = True
    rules = list(app.url_map.iter_rules())
    unknown = PUBLIC_ENDPOINTS - {r.endpoint for r in rules}
    if unknown:
        logger.warning("PUBLIC_ENDPOINTS names unknown endpoints: %s", sorted(unknown))
    public = [r.endpoint for r in rules if r.endpoint in PUBLIC_ENDPOINTS]
    logger.info(
        "auth active: %d routes protected, %d public (%s)",
        len(rules) - len(public), len(public), ", ".join(sorted(set(public))),
    )


def _require_login():
    """Deny-by-default guard, run before every request on every blueprint."""
    from flask import current_app
    _audit_once(current_app)

    if request.method == "OPTIONS":
        return None
    endpoint = request.endpoint
    if endpoint is None:
        return None  # no route matched; let Flask return its 404
    if endpoint in PUBLIC_ENDPOINTS:
        return None

    user_id = session.get("user_id")
    if not user_id:
        return _deny()
    try:
        user = db.get_user(user_id)
    except Exception as exc:  # noqa: BLE001 - fail closed on a DB problem
        logger.error("session user lookup failed: %s", exc)
        return _deny(503)
    if user is None:
        session.clear()  # user deleted since the session was issued
        return _deny()

    g.user = user

    # Role gate. Enforced here rather than only by decorators, so a route that
    # nobody decorated is still closed to a client — the same reason login is
    # enforced here rather than by @login_required on each view.
    role = str(user.get("role") or "client")
    if endpoint in ADMIN_ENDPOINTS and role != "admin":
        return _forbid(endpoint, role)
    if role == "client" and endpoint not in CLIENT_ENDPOINTS:
        return _forbid(endpoint, role)

    db.set_current_tenant(user["tenant_id"])
    return None


def _clear_tenant(_exc=None):
    """
    Unbind the tenant at the end of every request.

    Worker threads are reused, and a contextvar left set would carry this
    client's tenant into whatever request the thread picks up next.
    """
    try:
        db.set_current_tenant(None)
    except Exception:  # noqa: BLE001
        pass


def init_auth(app) -> None:
    secret = os.getenv("FLASK_SECRET_KEY")
    if not secret:
        secret = secrets.token_hex(32)
        logger.warning(
            "FLASK_SECRET_KEY is not set — generated a random key. Sessions "
            "will not survive a restart. Set it in .env before deploying."
        )
    app.secret_key = secret

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        # Lax still sends the cookie on top-level navigation but not on
        # cross-site POSTs, which covers the CSRF cases that matter here.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=SESSION_HOURS * 3600,
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    )

    app.register_blueprint(auth_bp)
    app.before_request(_require_login)
    app.teardown_request(_clear_tenant)

    # The route table is still filling at this point; _audit_once() reports the
    # real split on the first request.
    logger.info("auth enabled (deny by default)")
