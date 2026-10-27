"""Authentication for the operator console.

Everything that decides whether a request may proceed lives here, so the
answer to "what protects this app?" is one file rather than a before_request
buried in app.py next to the Companies House proxy.

Three ways in, all checking the same APP_AUTH_TOKEN:

  1. A signed session cookie  - browsers, set by the login page below
  2. Authorization: Bearer    - scripts, curl, deploy checks
  3. HTTP Basic, password=token - kept so existing callers do not break

The login page exists because this console is now reachable from the public
internet at a normal URL, and HTTP Basic makes the browser show its own
dialog asking for a username AND a password where the username is ignored.
That is a confusing thing to hand a non-technical operator. A single
password box, a "stay signed in" cookie and a logout button are not
cosmetic here; they are the difference between someone getting in and
someone giving up.

Fails CLOSED: with APP_AUTH_TOKEN unset the app serves nothing but /health.
A system that spends money must not default to open.

What the session stores is a SHA-256 fingerprint of the token, never the
token. Two consequences, both wanted: the cookie is useless to anyone who
steals it without also having the signing key, and rotating APP_AUTH_TOKEN
invalidates every outstanding session automatically.
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

from flask import (Response, redirect, render_template_string, request,
                   session, url_for)

log = logging.getLogger(__name__)

# Paths reachable without credentials. Deliberately tiny, and deliberately
# NOT including anything under /static: the login page carries its own
# inline CSS so that logging in requires exactly one unauthenticated path,
# not a page plus whatever assets it drags in.
AUTH_EXEMPT = {"/health", "/login", "/logout"}

_SESSION_KEY = "op"          # session field holding the token fingerprint

# Brute force protection. The console is now public, and a single shared
# secret with unlimited guesses is a different proposition from one behind
# Tailscale. In-memory like the DUNS and import registries, which is why
# gunicorn.conf.py pins workers = 1.
_LOGIN_WINDOW = 300.0        # seconds
_LOGIN_MAX_FAILS = 8         # per client, per window
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_ip() -> str:
    """The caller's address, trusting X-Forwarded-For ONLY from loopback.

    Caddy terminates TLS and proxies to 127.0.0.1, so in production the
    useful address is in the header. Trusting that header unconditionally
    would let anyone reset their own rate-limit bucket by making one up, so
    it is honoured only when the immediate peer is loopback - i.e. when the
    request really did come through the local reverse proxy.
    """
    peer = request.remote_addr or ""
    if peer in ("127.0.0.1", "::1"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def _rate_limited(ip: str) -> int:
    """Seconds the caller must wait, or 0 if they may try."""
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


def _wants_html() -> bool:
    """True when a browser is navigating, rather than a script calling.

    Decides whether an unauthenticated request gets a redirect to the login
    page or a 401 JSON body. Getting this wrong either way is user-visible:
    a redirect to an API caller looks like a successful 200 with HTML in it,
    and a 401 to a browser is a blank error page instead of a login form.
    """
    if request.path.startswith("/api/"):
        return False
    accept = request.headers.get("Accept", "")
    return "text/html" in accept or accept in ("", "*/*")


def _safe_next(target: str | None) -> str:
    """Only ever redirect within this site.

    A bare `next` taken from the query string is an open redirect: a link
    to /login?next=https://evil.example would bounce the operator off-site
    immediately after they typed the password.
    """
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


_LOGIN_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — Company Pipeline</title>
<style>
  :root{--bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--border:#30363d;
        --text:#e6edf3;--muted:#8b949e;--blue:#2f81f7;--red:#f85149}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
       color:var(--text);min-height:100vh;display:flex;align-items:center;
       justify-content:center;padding:20px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
        padding:32px;width:100%;max-width:380px}
  h1{font-size:19px;font-weight:600;margin-bottom:6px}
  h1 span{color:var(--blue)}
  .sub{color:var(--muted);font-size:13px;margin-bottom:22px}
  label{display:block;font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px}
  input[type=password]{width:100%;padding:10px 12px;background:var(--surface2);
        border:1px solid var(--border);border-radius:6px;color:var(--text);
        font-size:14px;font-family:inherit}
  input[type=password]:focus{outline:none;border-color:var(--blue)}
  .row{display:flex;align-items:center;gap:8px;margin:14px 0 20px;
       font-size:13px;color:var(--muted)}
  button{width:100%;padding:10px;background:var(--blue);border:1px solid var(--blue);
         border-radius:6px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;
         font-family:inherit}
  button:hover{background:#3990ff}
  .err{background:rgba(248,81,73,.12);border:1px solid var(--red);color:#ff8b82;
       border-radius:6px;padding:10px 12px;font-size:13px;margin-bottom:18px}
</style></head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Company <span>Pipeline</span></h1>
    <p class="sub">Enter the access password to continue.</p>
    {{ error_block|safe }}
    <input type="hidden" name="next" value="{{ next_target }}">
    <label for="pw">Password</label>
    <input id="pw" type="password" name="password" autocomplete="current-password"
           autofocus required>
    <div class="row">
      <input type="checkbox" id="remember" name="remember" value="1" checked>
      <label for="remember" style="margin:0;font-weight:500;cursor:pointer">
        Stay signed in on this device
      </label>
    </div>
    <button type="submit">Sign in</button>
  </form>
</body></html>"""


def install_auth(app, token: str | None = None) -> None:
    """Attach the gate and the login/logout routes to `app`."""
    auth_token = os.getenv("APP_AUTH_TOKEN", "") if token is None else token
    fingerprint = _fingerprint(auth_token) if auth_token else ""

    # The session cookie is signed with this. Left unset it is random per
    # process, so every restart silently signs everyone out - which looks
    # like "the password stopped working". Warn rather than let that be a
    # mystery later.
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    if not os.getenv("FLASK_SECRET_KEY"):
        log.warning("FLASK_SECRET_KEY is not set: sessions will not survive a "
                    "restart and every operator will be signed out on deploy")

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,      # JavaScript cannot read it
        SESSION_COOKIE_SAMESITE="Lax",     # not sent on cross-site POSTs
        # Secure means the browser only sends it over HTTPS. True in
        # production, which is why tests must opt out explicitly rather
        # than the default being the lax one.
        SESSION_COOKIE_SECURE=os.getenv("ALLOW_INSECURE_COOKIE") != "1",
    )

    def token_ok(presented: str) -> bool:
        if not auth_token or not presented:
            return False
        return secrets.compare_digest(presented, auth_token)

    def _unconfigured() -> Response:
        return Response(
            json.dumps({"error": "APP_AUTH_TOKEN is not set; refusing to serve"}),
            status=503, mimetype="application/json")

    @app.before_request
    def _require_operator_token():
        if request.path in AUTH_EXEMPT:
            return None
        if not auth_token:
            return _unconfigured()

        # 1. Browser session.
        if session.get(_SESSION_KEY) == fingerprint:
            return None

        # 2. Bearer token.
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer ") and token_ok(header[7:].strip()):
            return None

        # 3. HTTP Basic, password = token. Retained so every script, curl
        #    invocation and deploy check written before the login page
        #    keeps working unchanged.
        basic = request.authorization
        if basic is not None and basic.password and token_ok(basic.password):
            return None

        if _wants_html():
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return Response(
            json.dumps({"error": "unauthorized"}),
            status=401, mimetype="application/json",
            headers={"WWW-Authenticate": 'Basic realm="company-registration"'})

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not auth_token:
            return _unconfigured()

        next_target = _safe_next(request.values.get("next"))
        error = ""

        if request.method == "POST":
            ip = _client_ip()
            wait = _rate_limited(ip)
            if wait:
                log.warning("login rate-limited ip=%s wait=%ss", ip, wait)
                error = (f"Too many attempts. Try again in about "
                         f"{max(1, wait // 60)} minute(s).")
            elif token_ok(request.form.get("password", "")):
                _clear_failures(ip)
                session.clear()
                session[_SESSION_KEY] = fingerprint
                # Unchecked means a browser-session cookie that dies when
                # the browser closes; checked means it persists.
                session.permanent = bool(request.form.get("remember"))
                log.info("login succeeded ip=%s", ip)
                return redirect(next_target)
            else:
                _record_failure(ip)
                log.warning("login failed ip=%s", ip)
                error = "That password is not correct."

        error_block = (f'<div class="err">{html.escape(error)}</div>'
                       if error else "")
        status = 429 if "Too many attempts" in error else (
            401 if error else 200)
        return render_template_string(
            _LOGIN_HTML,
            error_block=error_block,
            next_target=html.escape(next_target, quote=True),
        ), status

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))


# Backwards-compatible alias: app.py re-exports this and existing suites
# assert against it by this name.
_AUTH_EXEMPT = AUTH_EXEMPT
