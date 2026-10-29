#!/usr/bin/env bash
# Put the ibenty credentials into the server's .env, restart the app, and
# prove the server can actually log in with them.
#
#   cd /opt/company_registration
#   git pull
#   bash deploy/set_ibenty_env.sh
#
# It prompts for the two values. Nothing is taken from the command line by
# default and nothing is written into this file, because a credential in a
# repository is published the moment it is pushed - this project already
# has one Namecheap key that has to be regenerated for exactly that
# reason. The prompt uses `read -s`, so the password does not reach the
# terminal, the shell history, or `ps`.
#
# Idempotent: run it again to change the values. An existing .env is backed
# up first, and only the credential lines are touched.
#
# The service used to be called xbinder and the variables used to be named
# XBINDER_EMAIL / XBINDER_PASSWORD. pipeline.py still reads those if the
# current ones are absent, so an un-migrated box keeps working; this script
# writes the current names and REMOVES the old ones, so there is one
# source of truth rather than two that can disagree. python-dotenv keeps
# the last occurrence of a key, so duplicates do not merely look untidy -
# they silently decide which value wins.
set -euo pipefail

# Overridable so the whole script can be exercised in a sandbox. Both
# default to what the server actually uses; test_ibenty_rename.py sets them
# to run this file end to end without a network or a real .env, which is the
# only way to check the control flow below rather than grep for it.
APP_DIR="${APP_DIR:-/opt/company_registration}"
ENV_FILE="$APP_DIR/.env"
SITE_API="${SITE_API:-https://apis.ibenty.com/api}"
PY="${PY:-}"

say() { printf '\n=== %s\n' "$*"; }
ok()  { printf '    [ok] %s\n' "$*"; }
bad() { printf '    [!!] %s\n' "$*"; }

[[ -d "$APP_DIR" ]] || { bad "$APP_DIR does not exist - is this the server?"; exit 2; }

# ── 1. collect ──────────────────────────────────────────────────────────
say "1. credentials"
EMAIL="${1:-}"
PASSWORD="${2:-}"
if [[ -n "$EMAIL" || -n "$PASSWORD" ]]; then
  bad "values passed as arguments - they are now in your shell history"
  bad "and were visible in 'ps'. Change them later, or re-run with no"
  bad "arguments and type them at the prompt instead."
else
  read -r -p "    ibenty email: " EMAIL
  read -r -s -p "    ibenty password: " PASSWORD; echo
fi
[[ -n "$EMAIL" && -n "$PASSWORD" ]] || { bad "both values are required"; exit 2; }
# A value containing a newline would corrupt the file into two broken
# lines, and a '#' at the start would be silently read as a comment.
case "$EMAIL$PASSWORD" in
  *$'\n'*) bad "a value contains a newline; .env cannot represent that"; exit 2;;
esac
ok "email is ${#EMAIL} chars, password is ${#PASSWORD} chars"

# ── 2. write ────────────────────────────────────────────────────────────
say "2. .env"
if [[ -f "$ENV_FILE" ]]; then
  BACKUP="$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "$ENV_FILE" "$BACKUP"
  ok "backed up to $BACKUP"
else
  bad "$ENV_FILE did not exist - creating it"
  : > "$ENV_FILE"
fi

HAD_OLD=$(grep -c -E '^[[:space:]]*(XBINDER_EMAIL|XBINDER_PASSWORD)=' "$ENV_FILE" || true)

TMP="$(mktemp "${ENV_FILE}.new.XXXXXX")"
chmod 600 "$TMP"
# Drop both the current and the superseded names, then write the current
# ones once. Dropping the current names too is what makes a re-run safe.
grep -v -E '^[[:space:]]*(IBENTY_EMAIL|IBENTY_PASSWORD|XBINDER_EMAIL|XBINDER_PASSWORD)=' \
  "$ENV_FILE" > "$TMP" || true
# Ensure the file ends with a newline before appending, or the first new
# line would be glued onto the last existing one.
[[ -s "$TMP" && -n "$(tail -c1 "$TMP")" ]] && printf '\n' >> "$TMP"
{
  printf 'IBENTY_EMAIL=%s\n' "$EMAIL"
  printf 'IBENTY_PASSWORD=%s\n' "$PASSWORD"
} >> "$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"
chown pipeline:pipeline "$ENV_FILE" 2>/dev/null || bad "could not chown to pipeline"
ok "IBENTY_EMAIL and IBENTY_PASSWORD written (mode $(stat -c %a "$ENV_FILE"), owner $(stat -c %U "$ENV_FILE"))"
ok "no duplicate keys: $(grep -c -E '^(IBENTY_EMAIL|IBENTY_PASSWORD)=' "$ENV_FILE") lines, expected 2"
if [[ "$HAD_OLD" -gt 0 ]]; then
  ok "removed $HAD_OLD superseded XBINDER_ line(s); the backup still has them"
fi
left=$(grep -c -E '^[[:space:]]*(XBINDER_EMAIL|XBINDER_PASSWORD)=' "$ENV_FILE" || true)
[[ "$left" -eq 0 ]] || { bad "$left XBINDER_ line(s) survived - two sources of truth"; exit 1; }

# ── 3. pick the interpreter the app itself uses ──────────────────────────
say "3. interpreter"
if [[ -z "$PY" ]]; then
  for cand in "$APP_DIR/.venv/bin/python" "$APP_DIR/venv/bin/python" "$(command -v python3 || true)"; do
    [[ -n "$cand" && -x "$cand" ]] && { PY="$cand"; break; }
  done
fi
[[ -n "$PY" && -x "$PY" ]] || { bad "no python found"; exit 1; }
ok "using $PY"

# ── 4. prove it ─────────────────────────────────────────────────────────
# Reading the file back only proves the file was written. The question is
# whether the app, loading it the way it actually loads it, can log in to
# ibenty from THIS machine - which tests egress and the credential at the
# same time. The token is never printed, only its length.
say "4. does the server log in to ibenty with these values"
cd "$APP_DIR"
# Written to a real file rather than piped in on stdin. load_dotenv() with
# no argument calls find_dotenv(), which walks back through the CALLER's
# stack frames to locate .env - and a script read from stdin has no caller
# frame, so it dies on `assert frame.f_back is not None`. Measured: the
# identical code fails from stdin and succeeds from a file beside .env.
#
# A file in APP_DIR is also the faithful test. The point of this step is
# whether the app, loading .env the way it actually loads it, can log in;
# passing an explicit path instead would skip the discovery the app relies
# on.
VERIFY="$(mktemp "$APP_DIR/.verify_ibenty.XXXXXX.py")"
trap 'rm -f "$VERIFY"' EXIT
cat > "$VERIFY" <<'PY'
import os, sys
from dotenv import load_dotenv
load_dotenv()
email = os.environ.get("IBENTY_EMAIL", "")
password = os.environ.get("IBENTY_PASSWORD", "")
print(f"    dotenv sees IBENTY_EMAIL={email!r}")
print(f"    dotenv sees IBENTY_PASSWORD=<{len(password)} chars>")
if not (email and password):
    print("    [!!] the app cannot see the credentials"); sys.exit(1)
import requests
base = os.environ["IB_API"]
try:
    r = requests.post(f"{base}/auth/login",
                      json={"email": email, "password": password}, timeout=30)
except Exception as e:
    print(f"    [!!] cannot reach {base}: {type(e).__name__}: {e}"); sys.exit(1)
print(f"    HTTP {r.status_code} from {base}/auth/login")
if r.status_code != 200:
    print(f"    [!!] login refused: {r.text[:200]}"); sys.exit(1)
body = r.json()
tok = body.get("token") or ""
if not tok:
    print(f"    [!!] 200 but no token; keys were {sorted(body)}"); sys.exit(1)
print(f"    [ok] token received (<{len(tok)} chars>), userId={body.get('userId')}")

# A wrong password must be refused, or the check above proves nothing about
# the credential - only that the endpoint answers.
r2 = requests.post(f"{base}/auth/login",
                   json={"email": email, "password": password + "-wrong"},
                   timeout=30)
if r2.status_code == 200:
    print("    [!!] a WRONG password also returned 200 - this endpoint is "
          "not authenticating, so the success above means nothing")
    sys.exit(1)
print(f"    [ok] a wrong password is refused (HTTP {r2.status_code}), so "
      f"the success above is real")
PY

# `rc=$?` on its own line does not work here. Under `set -e` the shell
# terminates at the failing command itself, so that line never runs and the
# message below never prints - the operator gets a bare traceback and no
# guidance. Measured both ways. `|| rc=$?` lets the failure be caught.
#
# Still not a pipe: a pipe would hand back the status of the last stage,
# which this project has already been bitten by.
rc=0
IB_API="$SITE_API" "$PY" "$VERIFY" || rc=$?
rm -f "$VERIFY"
trap - EXIT
[[ $rc -eq 0 ]] || { bad "verification failed - .env backup is beside the file"; exit 1; }

# ── 5. restart ──────────────────────────────────────────────────────────
say "5. restart"
# The app loads .env at import, so a running process still holds the old
# values until it is restarted.
if systemctl list-unit-files company-registration.service &>/dev/null; then
  systemctl restart company-registration
  sleep 4
  if systemctl is-active --quiet company-registration; then
    ok "company-registration restarted and is active"
  else
    bad "company-registration did NOT come back:"
    bad "  journalctl -u company-registration -n 40 --no-pager"
    exit 1
  fi
  # /health is the one route auth.py:50 exempts from the login gate
  # (AUTH_EXEMPT), so a 200 here means the app is serving, not redirecting.
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
         http://127.0.0.1:8000/health) || code="connect-failed"
  [[ "$code" == "200" ]] && ok "app answers on 127.0.0.1:8000/health ($code)" \
                         || bad "app health returned $code"
else
  bad "company-registration.service not installed - skipping restart"
fi

say "done"
echo "    The ibenty credentials are in $ENV_FILE and the server has"
echo "    proved it can log in with them."
