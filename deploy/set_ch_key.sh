#!/usr/bin/env bash
# Put the Companies House API key into the server's .env, restart the app,
# and prove the key actually works from this machine.
#
#   cd /opt/company_registration
#   git pull
#   bash deploy/set_ch_key.sh
#
# It prompts for the key. Nothing is taken from the command line by default
# and nothing is written into this file, because a credential in a
# repository is published the moment it is pushed - this project already
# has one Namecheap key that has to be regenerated for exactly that reason.
# The prompt uses `read -s`, so the key does not reach the terminal, the
# shell history, or `ps`.
#
# Idempotent: run it again to change the value. An existing .env is backed
# up first and only the one line is touched - the key is removed before it
# is rewritten, because python-dotenv keeps the LAST occurrence, so a
# duplicate does not merely look untidy, it silently decides which value
# wins.
#
# Without this key app.py:116 leaves API_KEY as None and every Companies
# House call - company details, officers, PSCs, filings, and the director
# date of birth lookup at ibenty_auto.py:156 - fails. ch_get passes it
# straight to requests' auth=, so the symptom is a 401 rather than a clear
# message about configuration.
set -euo pipefail

# Overridable so the whole script can be exercised in a sandbox; both
# default to what the server uses. test_deploy_scripts.py sets them to run
# this file end to end without a network, which is the only way to check
# the control flow rather than grep for it.
APP_DIR="${APP_DIR:-/opt/company_registration}"
ENV_FILE="$APP_DIR/.env"
CH_BASE="${CH_BASE:-https://api.company-information.service.gov.uk}"
PY="${PY:-}"

say() { printf '\n=== %s\n' "$*"; }
ok()  { printf '    [ok] %s\n' "$*"; }
bad() { printf '    [!!] %s\n' "$*"; }

[[ -d "$APP_DIR" ]] || { bad "$APP_DIR does not exist - is this the server?"; exit 2; }

# ── 1. collect ──────────────────────────────────────────────────────────
say "1. credential"
KEY="${1:-}"
if [[ -n "$KEY" ]]; then
  bad "the key was passed as an argument - it is now in your shell history"
  bad "and was visible in 'ps'. Regenerate it later, or re-run with no"
  bad "arguments and type it at the prompt instead."
else
  read -r -s -p "    Companies House API key: " KEY; echo
fi
[[ -n "$KEY" ]] || { bad "the key is required"; exit 2; }
case "$KEY" in
  *$'\n'*) bad "the value contains a newline; .env cannot represent that"; exit 2;;
esac
ok "key is ${#KEY} chars"

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

TMP="$(mktemp "${ENV_FILE}.new.XXXXXX")"
chmod 600 "$TMP"
grep -v -E '^[[:space:]]*COMPANIES_HOUSE_API_KEY=' "$ENV_FILE" > "$TMP" || true
# Ensure a trailing newline before appending, or the new line would be
# glued onto the last existing one.
[[ -s "$TMP" && -n "$(tail -c1 "$TMP")" ]] && printf '\n' >> "$TMP"
printf 'COMPANIES_HOUSE_API_KEY=%s\n' "$KEY" >> "$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"
chown pipeline:pipeline "$ENV_FILE" 2>/dev/null || bad "could not chown to pipeline"
ok "COMPANIES_HOUSE_API_KEY written (mode $(stat -c %a "$ENV_FILE"), owner $(stat -c %U "$ENV_FILE"))"
n=$(grep -c -E '^COMPANIES_HOUSE_API_KEY=' "$ENV_FILE" || true)
[[ "$n" -eq 1 ]] || { bad "$n COMPANIES_HOUSE_API_KEY lines, expected 1"; exit 1; }
ok "exactly one COMPANIES_HOUSE_API_KEY line"

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
# whether the app, loading .env the way it actually loads it, can call
# Companies House from THIS machine - which tests egress and the credential
# at the same time. The key is never printed, only its length.
say "4. does the server reach Companies House with this key"
cd "$APP_DIR"
# A real file, not a heredoc on stdin: load_dotenv() with no argument calls
# find_dotenv(), which walks back through the CALLER's stack frames, and a
# script read from stdin has no caller frame - it dies on
# `assert frame.f_back is not None`. A file in APP_DIR is also the faithful
# test, because it is how the app loads .env.
VERIFY="$(mktemp "$APP_DIR/.verify_ch.XXXXXX.py")"
trap 'rm -f "$VERIFY"' EXIT
cat > "$VERIFY" <<'PY'
import os, sys
from dotenv import load_dotenv
load_dotenv()
key = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
print(f"    dotenv sees COMPANIES_HOUSE_API_KEY=<{len(key)} chars>")
if not key:
    print("    [!!] the app cannot see the key"); sys.exit(1)
import requests
base = os.environ["CH_BASE_URL"]
try:
    r = requests.get(f"{base}/search/companies",
                     params={"q": "ltd", "items_per_page": 1},
                     auth=(key, ""), timeout=30)
except Exception as e:
    print(f"    [!!] cannot reach {base}: {type(e).__name__}: {e}"); sys.exit(1)
print(f"    HTTP {r.status_code} from {base}/search/companies")
if r.status_code != 200:
    print(f"    [!!] refused: {r.text[:200]}"); sys.exit(1)
print(f"    [ok] {len(r.json().get('items', []))} result(s) returned")

# A wrong key must be refused, or the success above only says the endpoint
# answers. The last character is changed, so the shape stays valid.
wrong = key[:-1] + ("0" if key[-1] != "0" else "1")
r2 = requests.get(f"{base}/search/companies",
                  params={"q": "ltd", "items_per_page": 1},
                  auth=(wrong, ""), timeout=30)
if r2.status_code == 200:
    print("    [!!] a WRONG key also returned 200 - this endpoint is not "
          "authenticating, so the success above means nothing")
    sys.exit(1)
print(f"    [ok] a wrong key is refused (HTTP {r2.status_code}), so the "
      f"success above is real")
PY

# `rc=$?` on its own line is unreachable under `set -e`: the shell exits at
# the failing command, so that line never runs and the message below never
# prints. `|| rc=$?` lets the failure be caught. Still not a pipe, which
# would hand back the status of the last stage.
rc=0
CH_BASE_URL="$CH_BASE" "$PY" "$VERIFY" || rc=$?
rm -f "$VERIFY"
trap - EXIT
[[ $rc -eq 0 ]] || { bad "verification failed - .env backup is beside the file"; exit 1; }

# ── 5. restart ──────────────────────────────────────────────────────────
say "5. restart"
# app.py reads COMPANIES_HOUSE_API_KEY at import (app.py:116), so a running
# process still holds the old value - None - until it is restarted.
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
  # /health is the one route auth.py:50 exempts from the login gate.
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
         http://127.0.0.1:8000/health) || code="connect-failed"
  [[ "$code" == "200" ]] && ok "app answers on 127.0.0.1:8000/health ($code)" \
                         || bad "app health returned $code"
else
  bad "company-registration.service not installed - skipping restart"
fi

say "done"
echo "    The Companies House key is in $ENV_FILE and the server has"
echo "    proved it can use it."
