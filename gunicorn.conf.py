"""Gunicorn configuration for the company_registration pipeline UI.

Run with:  gunicorn -c gunicorn.conf.py app:app

On Linux, prefer:  xvfb-run -a gunicorn -c gunicorn.conf.py app:app

The dashboard's own DUNS stage does not need a display: it goes through
run_pipeline.get_duns_number, which is a plain HTTP call to D&B's JSON
endpoint. But the wizard's DUNS button (POST /api/duns/request) and the
stealth endpoints in app.py drive a real browser, and that path is forced
headed because D&B blocks headless (browser_stealth.py, the comment in
_run_duns_lookup_async). A headed Chromium needs an X or Wayland display;
browser_stealth._require_display refuses with an actionable message when
there is none, rather than letting Chromium die with a closed-browser error.

Measured cost of that headed path: ~1117 MB peak RSS for Chromium plus
Xvfb, against ~742 MB headless, so size the host for the headed figure if
the wizard's DUNS button is going to be used.
"""

# workers MUST remain 1.
#
# The stage runner keeps its entire job table in process memory:
# api_v2.py:67-69 defines _JOBS, _LOCK and _CO_LOCKS as module globals, and
# stages execute in daemon threads (api_v2.py:173, api_v2.py:785). With a
# second worker:
#   - GET /api/v2/company/<cn>/job/<stage> would report "idle" for a job that
#     is in fact running in the other worker, and
#   - the per-company lock _co_lock(cn) would no longer serialise stages, so
#     two workers could run the billed domain stage for one company at once.
# This is a correctness constraint, not a tuning preference.
workers = 1

# Concurrency therefore has to come from threads inside the single worker.
# Some routes are synchronous and slow: wizard_routes.py:345 calls
# stealth_request_duns() inside the request and drives a real browser. With
# one worker and one thread, such a request would block the whole UI.
worker_class = "gthread"
threads = 8

# NOTE: this value is NOT derived from measured timings - no real DUNS run has
# been timed in this environment. It is set high enough that a browser-driven
# request is not killed mid-flight. Measure an actual run and tighten it.
timeout = 1800
graceful_timeout = 30
keepalive = 5

# Bind to loopback only; a reverse proxy in front terminates TLS and is the
# only thing that should be publicly reachable.
bind = "127.0.0.1:8000"

accesslog = "-"
errorlog = "-"
loglevel = "info"

# preload_app stays False: the app opens a database connection and writes
# pipeline_output/ at import time (wizard_routes.py:128-134), which should
# happen in the worker that will serve requests, not in the master.
preload_app = False
