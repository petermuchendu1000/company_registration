"""The request gate, and the pages that let a person through it.

Everything that decides whether a request may proceed lives here, so the
answer to "what protects this app?" is one file rather than a decorator
remembered on some routes and forgotten on others. The gate is a single
before_request: a route added tomorrow is covered by default, and opening
one up has to be done deliberately by adding it to AUTH_EXEMPT.

Two kinds of caller:

  SERVICE - Authorization: Bearer <APP_AUTH_TOKEN>, or HTTP Basic with the
      token as the password. Treated as superadmin. This is what scripts,
      curl, deploy_check.py and the test suites use, and it is why the
      shared token did not disappear when accounts arrived: a machine
      should not need a person's password.

  PERSON - a signed session cookie naming a row in app_users. Carries a
      role and a status, and is re-checked on every request.

The session stores the user id AND their session_epoch. users.py bumps
that epoch on any password change, suspension or rejection, so those
actions cut off an already-logged-in browser immediately instead of
leaving it working until the cookie happens to expire. A stolen cookie
also stops working the moment the password behind it changes.

Fails CLOSED throughout: no APP_AUTH_TOKEN and no superadmin means the app
serves nothing but /health.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import secrets
import threading
import time

from flask import (Response, g, redirect, render_template_string, request,
                   session, url_for)

import users

log = logging.getLogger(__name__)

# Reachable without credentials. Deliberately tiny. Every page served from
# here carries inline CSS and references no external asset, so being signed
# out exposes exactly these paths and nothing they drag in.
AUTH_EXEMPT = {"/health", "/login", "/logout", "/register"}

# Any path under these requires admin or above. Enforced in the gate rather
# than per-route: a new admin endpoint is protected the moment it exists,
# instead of when somebody remembers the decorator.
ADMIN_PREFIXES = ("/admin", "/api/admin")

_SID = "uid"          # session: user id
_SEPOCH = "ep"        # session: the user's session_epoch when they signed in
_SSVC = "svc"         # session: set for the legacy shared-token login

_LOGIN_WINDOW = 300.0
_LOGIN_MAX_FAILS = 8
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


# ── Rate limiting ───────────────────────────────────────────────────────────
def _client_ip() -> str:
    """The caller's address, trusting X-Forwarded-For ONLY from loopback.

    Caddy terminates TLS and proxies to 127.0.0.1, so in production the
    useful address is in the header. Honouring that header from any peer
    would let anyone reset their own rate-limit bucket by inventing one,
    which would make the limit below worth nothing.
    """
    peer = request.remote_addr or ""
    if peer in ("127.0.0.1", "::1"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def _rate_limited(ip: str) -> int:
    now = time.time()
    with _fails_lock:
        hits = [t for t in _fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        _fails[ip] = hits
        if len(hits) < _LOGIN_MAX_FAILS:
            return 0
        return int(_LOGIN_WINDOW - (now - hits[0])) + 1


def _record_failure(ip: str) -> None:
    now = time.time()
    with _fails_lock:
        hits = [t for t in _fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        hits.append(now)
        _fails[ip] = hits


def _clear_failures(ip: str) -> None:
    with _fails_lock:
        _fails.pop(ip, None)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _wants_html() -> bool:
    """True when a browser is navigating rather than a script calling.

    Decides redirect-to-login versus 401 JSON. Wrong either way is
    user-visible: a redirect handed to an API caller looks like a 200 with
    HTML in it, and a bare 401 handed to a browser is a blank error page.
    """
    if request.path.startswith("/api/"):
        return False
    accept = request.headers.get("Accept", "")
    return "text/html" in accept or accept in ("", "*/*")


def _safe_next(target: str | None) -> str:
    """Only ever redirect within this site.

    An unchecked `next` is an open redirect: /login?next=https://evil.example
    would bounce the operator off-site the instant they typed the password.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def current_user() -> dict | None:
    """The signed-in person, or None for a service caller."""
    return getattr(g, "user", None)


def is_service() -> bool:
    """True only for a caller that presented the shared service credential.

    Callers must ask this rather than inferring service-ness from
    current_user() being None. The two are not the same thing, and treating
    them as the same is how a request with no identity at all would acquire
    full control: mutation testing produced exactly that, by making the gate
    fall through for a session whose user row had been deleted.
    """
    return bool(getattr(g, "is_service", False))


def actor_name() -> str:
    u = current_user()
    if u:
        return u["email"]
    return "service-token" if getattr(g, "is_service", False) else "anonymous"


# ── Pages ───────────────────────────────────────────────────────────────────
_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} — Company Pipeline</title>
<style>
  :root{--bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--border:#30363d;
        --text:#e6edf3;--muted:#8b949e;--blue:#2f81f7;--red:#f85149;--green:#3fb950}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
       color:var(--text);min-height:100vh;display:flex;align-items:center;
       justify-content:center;padding:20px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
        padding:32px;width:100%;max-width:400px}
  h1{font-size:19px;font-weight:600;margin-bottom:6px}
  h1 span{color:var(--blue)}
  .sub{color:var(--muted);font-size:13px;margin-bottom:22px;line-height:1.5}
  label{display:block;font-size:12px;font-weight:600;color:var(--muted);
        margin-bottom:6px;margin-top:14px}
  input[type=text],input[type=email],input[type=password]{width:100%;padding:10px 12px;
        background:var(--surface2);border:1px solid var(--border);border-radius:6px;
        color:var(--text);font-size:14px;font-family:inherit}
  input:focus{outline:none;border-color:var(--blue)}
  .row{display:flex;align-items:center;gap:8px;margin:14px 0 4px;
       font-size:13px;color:var(--muted)}
  .row label{margin:0;font-weight:500;cursor:pointer}
  button{width:100%;padding:10px;background:var(--blue);border:1px solid var(--blue);
         border-radius:6px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;
         font-family:inherit;margin-top:20px}
  button:hover{background:#3990ff}
  .msg{border-radius:6px;padding:10px 12px;font-size:13px;margin-bottom:18px;
       line-height:1.45}
  .err{background:rgba(248,81,73,.12);border:1px solid var(--red);color:#ff8b82}
  .ok{background:rgba(63,185,80,.12);border:1px solid var(--green);color:#7ee787}
  .alt{margin-top:18px;font-size:13px;color:var(--muted);text-align:center}
  .alt a{color:var(--blue);text-decoration:none}
  .alt a:hover{text-decoration:underline}
</style></head>
<body><div class="card">{{ body|safe }}</div></body></html>"""


def _page(title: str, body: str, status: int = 200):
    return render_template_string(_SHELL, title=title, body=body), status


def _msg(text: str, kind: str = "err") -> str:
    return f'<div class="msg {kind}">{html.escape(text)}</div>' if text else ""


# ── Installation ────────────────────────────────────────────────────────────
def install_auth(app, token: str | None = None) -> None:
    service_token = os.getenv("APP_AUTH_TOKEN", "") if token is None else token
    service_fp = hashlib.sha256(service_token.encode()).hexdigest() if service_token else ""

    # Unset, this is random per process, so every restart silently signs
    # everyone out - which presents as "the password stopped working".
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    if not os.getenv("FLASK_SECRET_KEY"):
        log.warning("FLASK_SECRET_KEY is not set: sessions will not survive a "
                    "restart and everyone will be signed out on deploy")

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Secure means the browser only sends it over HTTPS. True in
        # production; a test must opt out explicitly rather than the lax
        # value being what you get by forgetting.
        SESSION_COOKIE_SECURE=os.getenv("ALLOW_INSECURE_COOKIE") != "1",
    )

    def service_ok(presented: str) -> bool:
        if not service_token or not presented:
            return False
        return secrets.compare_digest(presented, service_token)

    def _json(payload: dict, status: int) -> Response:
        return Response(json.dumps(payload), status=status,
                        mimetype="application/json")

    # ── the gate ──
    @app.before_request
    def _gate():
        g.user = None
        g.is_service = False

        if request.path in AUTH_EXEMPT:
            return None

        # 1. Service credential.
        header = request.headers.get("Authorization", "")
        basic = request.authorization
        if ((header.startswith("Bearer ") and service_ok(header[7:].strip()))
                or (basic is not None and basic.password
                    and service_ok(basic.password))):
            g.is_service = True
            return None

        # 2. A browser that signed in with the shared service token. Kept as
        #    a way back in when the account database is unreachable - the
        #    operator must not be locked out of their own server by a
        #    Supabase outage. Compared against the FINGERPRINT, so rotating
        #    APP_AUTH_TOKEN invalidates these sessions too.
        svc = session.get(_SSVC)
        if svc and service_fp and secrets.compare_digest(svc, service_fp):
            g.is_service = True
            return None

        # 3. A person.
        uid = session.get(_SID)
        if uid is None:
            return _deny()

        user = users.get_cached(uid)
        if user is None:
            session.clear()
            return _deny()

        # The epoch is what turns "suspended" and "password changed" into
        # immediate revocation rather than an eventual one.
        if session.get(_SEPOCH) != user["session_epoch"]:
            session.clear()
            return _deny("Your session ended because the account changed. "
                         "Please sign in again.")

        if user["status"] != users.STATUS_APPROVED:
            if user["status"] == users.STATUS_PENDING:
                if request.path == "/pending":
                    g.user = user
                    return None
                if _wants_html():
                    return redirect("/pending")
                return _json({"error": "account awaiting approval"}, 403)
            session.clear()
            return _deny("That account is not active. Contact an administrator.")

        g.user = user

        # A bootstrap password has by definition been written down. Force it
        # to be replaced before the account can do anything else.
        if user["must_change_password"] and request.path != "/change-password":
            if _wants_html():
                return redirect("/change-password")
            return _json({"error": "password change required"}, 403)

        if request.path.startswith(ADMIN_PREFIXES):
            if not users.at_least(user["role"], users.ROLE_ADMIN):
                log.warning("admin area refused for %s (role=%s) path=%s",
                            user["email"], user["role"], request.path)
                if _wants_html():
                    return _page("Not allowed", _msg(
                        "That area is for administrators only.") +
                        '<div class="alt"><a href="/">Back to the dashboard</a></div>',
                        403)
                return _json({"error": "administrator role required"}, 403)
        return None

    def _deny(message: str = ""):
        if _wants_html():
            target = url_for("login", next=request.full_path.rstrip("?"))
            if message:
                session["flash"] = message
            return redirect(target)
        return _json({"error": "unauthorized"}, 401)

    def _configured() -> bool:
        """False only when there is no way in at all."""
        if service_token:
            return True
        try:
            return users.count_active_superadmins() > 0
        except Exception:
            return False

    # ── login ──
    @app.route("/login", methods=["GET", "POST"])
    def login():
        next_target = _safe_next(request.values.get("next"))
        error = session.pop("flash", "")
        notice = request.args.get("registered") and (
            "Account created. An administrator must approve it before you "
            "can sign in.")

        if request.method == "POST":
            ip = _client_ip()
            wait = _rate_limited(ip)
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            if wait:
                log.warning("login rate-limited ip=%s wait=%ss", ip, wait)
                error = (f"Too many attempts. Try again in about "
                         f"{max(1, wait // 60)} minute(s).")
            elif service_token and not email and service_ok(password):
                # The shared token still works as a password with no email,
                # so an operator is never locked out by a database problem.
                _clear_failures(ip)
                session.clear()
                session[_SSVC] = service_fp
                session.permanent = bool(request.form.get("remember"))
                log.warning("sign-in with the SERVICE TOKEN from ip=%s", ip)
                return redirect(next_target)
            else:
                user = None
                try:
                    user = users.verify_password(email, password)
                except Exception as e:
                    log.error("login lookup failed: %s", e)
                    error = "The account database is unreachable. Try again shortly."
                if user is None and not error:
                    _record_failure(ip)
                    log.warning("login failed ip=%s email=%r", ip, email)
                    error = "Email or password is not correct."
                elif user is not None:
                    if user["status"] in (users.STATUS_SUSPENDED,
                                          users.STATUS_REJECTED):
                        error = "That account is not active. Contact an administrator."
                    else:
                        _clear_failures(ip)
                        session.clear()
                        session[_SID] = user["id"]
                        session[_SEPOCH] = user["session_epoch"]
                        session.permanent = bool(request.form.get("remember"))
                        try:
                            users.touch_login(user["id"])
                            users.invalidate_cache(user["id"])
                        except Exception:
                            pass
                        log.info("login ok ip=%s email=%s role=%s status=%s",
                                 ip, user["email"], user["role"], user["status"])
                        if user["status"] == users.STATUS_PENDING:
                            return redirect("/pending")
                        return redirect(next_target)

        if not _configured():
            return _page("Not configured", _msg(
                "No administrator account exists and no service token is set, "
                "so there is no way to sign in. Set SUPERADMIN_EMAIL and "
                "SUPERADMIN_PASSWORD in .env and restart."), 503)

        body = f"""
        <h1>Company <span>Pipeline</span></h1>
        <p class="sub">Sign in to continue.</p>
        {_msg(error)}{_msg(notice, "ok") if notice else ""}
        <form method="post" action="/login">
          <input type="hidden" name="next" value="{html.escape(next_target, quote=True)}">
          <label for="email">Email</label>
          <input id="email" type="email" name="email" autocomplete="username" autofocus>
          <label for="pw">Password</label>
          <input id="pw" type="password" name="password"
                 autocomplete="current-password" required>
          <div class="row">
            <input type="checkbox" id="remember" name="remember" value="1" checked>
            <label for="remember">Stay signed in on this device</label>
          </div>
          <button type="submit">Sign in</button>
        </form>
        <div class="alt">No account yet? <a href="/register">Request access</a></div>
        """
        status = 429 if "Too many attempts" in error else (401 if error else 200)
        return _page("Sign in", body, status)

    # ── register ──
    @app.route("/register", methods=["GET", "POST"])
    def register():
        error = ""
        if request.method == "POST":
            ip = _client_ip()
            wait = _rate_limited(ip)
            email = (request.form.get("email") or "").strip()
            name = (request.form.get("full_name") or "").strip()
            pw = request.form.get("password") or ""
            pw2 = request.form.get("password2") or ""
            if wait:
                error = (f"Too many attempts. Try again in about "
                         f"{max(1, wait // 60)} minute(s).")
            elif pw != pw2:
                error = "The two passwords do not match."
            else:
                try:
                    users.create_user(email, pw, full_name=name)
                    log.info("registration ip=%s email=%s (pending)", ip, email)
                    return redirect("/login?registered=1")
                except users.UserError as e:
                    # Registration failures are counted too: without this,
                    # the form is an unlimited oracle for which addresses
                    # already have accounts.
                    _record_failure(ip)
                    error = str(e)
                except Exception as e:
                    log.error("registration failed: %s", e)
                    error = "Could not create the account. Try again shortly."

        body = f"""
        <h1>Request <span>access</span></h1>
        <p class="sub">Create an account. An administrator has to approve it
        before you can sign in.</p>
        {_msg(error)}
        <form method="post" action="/register">
          <label for="name">Your name</label>
          <input id="name" type="text" name="full_name" autocomplete="name">
          <label for="email">Email</label>
          <input id="email" type="email" name="email" autocomplete="username" required>
          <label for="pw">Password</label>
          <input id="pw" type="password" name="password"
                 autocomplete="new-password" required>
          <label for="pw2">Password again</label>
          <input id="pw2" type="password" name="password2"
                 autocomplete="new-password" required>
          <button type="submit">Request access</button>
        </form>
        <div class="alt">Already have an account? <a href="/login">Sign in</a></div>
        """
        return _page("Request access", body, 401 if error else 200)

    # ── awaiting approval ──
    @app.route("/pending")
    def pending():
        u = current_user()
        who = html.escape(u["email"]) if u else ""
        body = f"""
        <h1>Waiting for <span>approval</span></h1>
        <p class="sub">Your account ({who}) has been created but an
        administrator has not approved it yet. You will be able to sign in
        once they do.</p>
        <div class="alt"><a href="/logout">Sign out</a></div>
        """
        return _page("Waiting for approval", body)

    # ── forced / voluntary password change ──
    @app.route("/change-password", methods=["GET", "POST"])
    def change_password():
        u = current_user()
        if u is None:
            return redirect(url_for("login"))
        error = ""
        forced = bool(u["must_change_password"])

        if request.method == "POST":
            current = request.form.get("current") or ""
            new = request.form.get("password") or ""
            new2 = request.form.get("password2") or ""
            if users.verify_password(u["email"], current) is None:
                error = "Your current password is not correct."
            elif new != new2:
                error = "The two new passwords do not match."
            elif new == current:
                error = "The new password must be different from the old one."
            else:
                try:
                    users.set_password(u["id"], new, actor=u["email"],
                                       must_change=False)
                    # set_password bumps the epoch, which invalidates THIS
                    # session too. Re-mint it from the fresh row so changing
                    # your own password does not sign you out.
                    fresh = users.get_by_id(u["id"])
                    session[_SID] = fresh["id"]
                    session[_SEPOCH] = fresh["session_epoch"]
                    log.info("password changed for %s", u["email"])
                    return redirect("/")
                except users.UserError as e:
                    error = str(e)

        lead = ("This account is using a password that was set up for it, so "
                "it has to be changed before you can go any further."
                if forced else "Choose a new password.")
        body = f"""
        <h1>Change <span>password</span></h1>
        <p class="sub">{lead}</p>
        {_msg(error)}
        <form method="post" action="/change-password">
          <label for="cur">Current password</label>
          <input id="cur" type="password" name="current"
                 autocomplete="current-password" required autofocus>
          <label for="pw">New password</label>
          <input id="pw" type="password" name="password"
                 autocomplete="new-password" required>
          <label for="pw2">New password again</label>
          <input id="pw2" type="password" name="password2"
                 autocomplete="new-password" required>
          <button type="submit">Change password</button>
        </form>
        """
        return _page("Change password", body, 400 if error else 200)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))


_AUTH_EXEMPT = AUTH_EXEMPT     # backwards-compatible alias
