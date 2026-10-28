"""Logging configuration for the company_registration app.

Structured application logs go to STDERR, deliberately:

  - stdout belongs to pipeline job output. api_v2.py:149 replaces sys.stdout
    with _Tee so the dashboard can show per-stage logs, and _Tee.write
    appends whatever it sees to the bound job's buffer. Emitting log records
    there would interleave application logging into those job logs.
  - gunicorn is configured with errorlog = "-" (gunicorn.conf.py), so stderr
    is what the host captures.

Call configure_logging() from every entry point: app.py for the web app, and
run_pipeline.py's __main__ for the subprocess the dashboard launches via
wizard_routes._resolve_pipeline_script.

Logger names worth knowing:
    money   - purchase claims and domain order outcomes. This is the audit
              trail for anything that spends money, and it is the one
              channel that should never be filtered out.
    app     - one line per HTTP request, with a request id.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(request_id)s%(message)s"


class _RequestIdFilter(logging.Filter):
    """Attach the current request id, or an empty string outside a request.

    Flask's `g` only exists inside a request context, and the pipeline runs
    as a bare subprocess with no Flask involved at all, so this has to
    degrade quietly: an exception raised here would be raised from inside a
    logging call.
    """

    def filter(self, record):
        rid = ""
        try:
            from flask import g, has_request_context
            if has_request_context():
                rid = getattr(g, "request_id", "") or ""
        except Exception:
            rid = ""
        record.request_id = f"[{rid}] " if rid else ""
        return True


def configure_logging() -> logging.Logger:
    """Install a stderr handler on the root logger, exactly once.

    Idempotent by design: calling it twice must not double every line, and
    both entry points may end up calling it in the same process.

    Level comes from LOG_LEVEL, default INFO. Note that INFO switches on the
    ~30 logger.info calls that already exist in browser_stealth.py and
    emitted nothing before, because no handler had ever been configured.
    That is the point of this module: the D&B automation was instrumented
    all along and the output was being discarded. LOG_LEVEL=WARNING turns it
    back down without touching code.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger()

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(),
                    logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    _CONFIGURED = True
    return root
