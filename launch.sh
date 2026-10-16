#!/usr/bin/env bash
#
# Start the Company App Factory server on Linux (the VPS deployment).
#
# Runs in the foreground so systemd, Docker or a terminal multiplexer can
# supervise it. launch.bat is the desktop equivalent and opens a browser;
# this does not, because a server has no desktop.
#
#   ./launch.sh              # bind 127.0.0.1:5000
#   HOST=0.0.0.0 ./launch.sh # bind all interfaces (put a TLS proxy in front)
#   ./launch.sh --check      # run the preflight checks and exit
#
# IMPORTANT — single process only. Background stage jobs live in an in-memory
# registry and their stdout is captured through one global tee, both per
# process. Under gunicorn with two or more workers a job started on worker A
# is invisible to worker B, so the dashboard would report "idle" for a build
# that is actually running. Threads are fine; processes are not. That holds
# until the job registry moves into Postgres.

set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
PYTHON="${PYTHON:-python3}"

fail=0
note()  { printf '  %s\n' "$1"; }
bad()   { printf '  MISSING  %s\n' "$1"; fail=1; }

printf '\nCompany App Factory\n===================\n\n'

# ── Preflight ──────────────────────────────────────────────────────────────
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
    note ".env loaded"
else
    note "no .env file (relying on the environment)"
fi

[ -n "${DATABASE_URL:-}" ] \
    && note "DATABASE_URL set" \
    || bad "DATABASE_URL — the datastore is Postgres; see db.py"

if [ -n "${FLASK_SECRET_KEY:-}" ]; then
    note "FLASK_SECRET_KEY set"
else
    # Not fatal: app.py generates one. It is fatal to sessions on restart.
    note "WARNING  FLASK_SECRET_KEY unset — sessions will not survive a restart"
fi

if [ -z "${SINGLE_TENANT_SLUG:-}" ]; then
    note "multi-tenant mode (every request must authenticate)"
else
    note "WARNING  SINGLE_TENANT_SLUG=${SINGLE_TENANT_SLUG} — all unbound work lands on that tenant"
fi

note "builds run ${MAX_CONCURRENT_BUILDS:-1} at a time (MAX_CONCURRENT_BUILDS)"

# Android toolchain: reported, never fatal. Every stage but the app build
# works without it, and a client may only want the earlier stages today.
if toolchain=$("$PYTHON" -c 'from apps.generator import toolchain; print(toolchain.describe())' 2>&1); then
    printf '%s\n' "$toolchain" | sed 's/^/  /'
else
    note "WARNING  Android toolchain unavailable — the app build stage will fail:"
    printf '%s\n' "$toolchain" | sed 's/^/      /'
fi

mkdir -p pipeline_output/logs

if [ "$fail" -ne 0 ]; then
    printf '\nRefusing to start: fix the items marked MISSING above.\n\n'
    exit 1
fi

if [ "${1:-}" = "--check" ]; then
    printf '\nPreflight passed.\n\n'
    exit 0
fi

if [ "$HOST" != "127.0.0.1" ] && [ "${FLASK_DEBUG:-0}" = "1" ]; then
    printf '\nRefusing to serve debug mode on %s.\n\n' "$HOST"
    exit 1
fi

printf "\n  Dashboard: http://%s:%s/app\n  Ctrl-C to stop.\n\n" "$HOST" "$PORT"

# exec: the Python process replaces this shell, so signals from the
# supervisor reach the server directly instead of being swallowed here.
exec "$PYTHON" -u app.py
