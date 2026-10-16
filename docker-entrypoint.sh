#!/bin/sh
# Container entrypoint.
#
# Under gunicorn the app's __main__ block never runs, so the startup work it
# does — validating configuration, creating the schema, moving pre-tenant
# company directories — has to happen here instead, once, before any worker
# accepts a request.
set -eu

log() { printf '  [entrypoint] %s\n' "$*"; }

wait_for_db() {
    # DATABASE_URL may name a host that is still starting. pg_isready
    # understands the URL form, so no parsing is needed.
    [ -n "${DATABASE_URL:-}" ] || return 0
    i=0
    until pg_isready -d "$DATABASE_URL" -q 2>/dev/null; do
        i=$((i + 1))
        if [ "$i" -ge 60 ]; then
            log "database not reachable after 60s: $(echo "$DATABASE_URL" | sed 's#://[^@]*@#://***@#')"
            return 1
        fi
        [ $((i % 10)) -eq 0 ] && log "waiting for the database (${i}s)"
        sleep 1
    done
    log "database is up"
}

prepare() {
    # Fails with a named setting rather than a stack trace from the first
    # query. Exit 2 means something required is missing.
    python3 config.py check || {
        status=$?
        [ "$status" -eq 2 ] && exit 2
    }
    wait_for_db
    python3 -c "import db; db.ensure_schema(); print('  [entrypoint] schema ready')"
    python3 - <<'PY'
import company_paths
notes = company_paths.migrate_all()
for note in notes:
    print(f"  [entrypoint] company-paths: {note}")
print(f"  [entrypoint] company-paths: {len(notes)} directory move(s)")
PY
}

case "${1:-serve}" in
    serve)
        prepare
        # One worker, deliberately. The build semaphore that caps concurrent
        # Gradle runs is held in process memory, and so is the live log tail
        # the dashboard streams; a second worker would double the build
        # concurrency and serve empty logs for jobs the other worker owns.
        # Scale with threads, not workers.
        : "${WEB_THREADS:=8}"
        : "${WEB_TIMEOUT:=1800}"
        # PORT is declared in config.py, so honour it here rather than
        # hardcoding 5000 and silently ignoring what the operator set. The
        # bind address is always 0.0.0.0: HOST defaults to loopback, which
        # inside a container would make the port unreachable.
        : "${PORT:=5000}"
        log "serving on 0.0.0.0:${PORT} (1 worker, ${WEB_THREADS} threads)"
        exec gunicorn \
            --bind "0.0.0.0:${PORT}" \
            --workers 1 \
            --threads "${WEB_THREADS}" \
            --timeout "${WEB_TIMEOUT}" \
            --graceful-timeout 60 \
            --access-logfile - \
            --error-logfile - \
            app:app
        ;;
    pipeline)
        # Run the CLI pipeline inside the same image: the Android SDK, the
        # Python deps and the data volume are all already here.
        shift
        prepare
        exec python3 pipeline.py "$@"
        ;;
    check)
        exec python3 config.py check --strict
        ;;
    shell)
        exec /bin/sh
        ;;
    *)
        exec "$@"
        ;;
esac
