"""
The cross-tenant console.

Everything here reads across every tenant, which is exactly what the rest of
the system is built to prevent: the data layer scopes each query to the
tenant bound on the request, and an operator belongs to one tenant. So these
endpoints do not use the bound tenant at all. They call the `admin_*`
helpers in db.py, which take no tenant and are reached from nowhere else.

Two gates, deliberately both:

  * `@admin_required` on every view, and
  * every endpoint registered in `auth.ADMIN_ENDPOINTS` by `init_admin_api`,
    so `auth`'s before_request hook refuses an operator even if a decorator
    is ever dropped from a view.

Registration is derived from the blueprint's own url_map entries rather than
written out by hand, because a list of endpoint names maintained in a second
place is a list that goes stale.

Nothing here runs a pipeline stage or spends money; the two writes change a
login's role and add addresses to a mailbox pool.
"""

from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import db
import company_paths
from auth import admin_required

ROOT = Path(__file__).resolve().parent

admin_api = Blueprint("admin_api", __name__)

#: A tenant whose pool is at or below this is flagged before it empties. The
#: email stage fails outright at zero, so the warning has to come early
#: enough to import more.
POOL_LOW = 10

#: A running job whose heartbeat is older than this is probably dead. The
#: worker writes one every few seconds; reclaim_stale_jobs() clears them at
#: startup, but between restarts they sit there looking busy.
STALE_RUNNING_SECONDS = 600

#: Walking every tenant's directory tree is the one part of this console that
#: touches disk. Past this many company directories it stops and says so,
#: rather than making the page slow in a way nobody can see.
ARTIFACT_SCAN_CAP = 2000


# ── filesystem-derived counts ───────────────────────────────────────────────

def _artifact_counts(budget: list[int]) -> dict[int, dict]:
    """
    Per tenant: how many companies have a built bundle, and how many have a
    committed Play release.

    Four of the eight stages are judged from files rather than from a row, so
    SQL cannot answer this. `budget` is a single-element list used as a
    mutable counter so the cap applies across every tenant, not per tenant.
    """
    out: dict[int, dict] = {}
    root = company_paths.companies_root()
    if not root.is_dir():
        return out

    slugs = {}
    try:
        for tenant in db.list_tenants():
            slugs[str(tenant.get("slug") or "")] = int(tenant["id"])
    except Exception:  # noqa: BLE001 - the console must still render
        return out

    for slug, tenant_id in slugs.items():
        built = released = scanned = 0
        tenant_dir = root / slug
        if tenant_dir.is_dir():
            for company_dir in sorted(tenant_dir.iterdir()):
                if budget[0] <= 0:
                    break
                if not company_dir.is_dir():
                    continue
                budget[0] -= 1
                scanned += 1
                app_dir = company_dir / "app"
                arts = app_dir / "artifacts"
                if arts.is_dir() and any(
                    f.suffix in (".aab", ".apk") for f in arts.iterdir()
                ):
                    built += 1
                receipt = app_dir / "publish_receipt.json"
                if receipt.is_file():
                    try:
                        import json
                        if json.loads(receipt.read_text(encoding="utf-8")).get("committed"):
                            released += 1
                    except (OSError, ValueError):
                        pass
        out[tenant_id] = {"apps_built": built, "released": released, "scanned": scanned}
    return out


# ── alerts ──────────────────────────────────────────────────────────────────

def _alerts(summary: list[dict], queue: dict) -> list[dict]:
    """
    What an admin opens this page to find out.

    Ordered by severity, then by how many it affects. Each alert names the
    tenant and what to do, because "3 failures" with no subject is a prompt
    to go hunting.
    """
    out: list[dict] = []

    # Grouped by kind, not one per tenant. An alert each would mean a page of
    # hundreds on an instance with many tenants — which is the state that most
    # needs reading quickly. Each group names the count and a few examples;
    # /tenants has the per-tenant detail.
    groups: dict[str, dict] = {}

    def flag(kind: str, severity: str, row: dict, action: str,
             phrase: str, group_phrase: str | None = None) -> None:
        """
        `phrase` describes one tenant; `group_phrase` describes several.

        They differ because a per-tenant number cannot speak for a group:
        "3 tenants — 1 mailbox left" would be the first tenant's count worn
        by all three.
        """
        g = groups.setdefault(kind, {
            "severity": severity, "kind": kind, "action": action,
            "tenants": [], "count": 0,
            "phrase": phrase, "group_phrase": group_phrase or phrase,
        })
        g["count"] += 1
        if len(g["tenants"]) < 3:
            g["tenants"].append(row["slug"])

    for row in summary:
        if row["pool_total"] and row["pool_available"] == 0:
            flag("pool_empty", "critical", row, "import addresses",
                 "mailbox pool is empty — the email stage cannot assign and will fail")
        elif row["pool_total"] == 0 and row["live_companies"]:
            flag("pool_missing", "critical", row, "import addresses",
                 "has companies but no mailbox pool at all")
        elif 0 < row["pool_available"] <= POOL_LOW and row["live_companies"]:
            # Only when there are companies to consume them. A pool filled
            # ahead of a tenant's first company is not a problem, and warning
            # about it trains people to skim past the list.
            n = row["pool_available"]
            flag("pool_low", "warning", row, "import addresses",
                 f"{n} mailbox{'' if n == 1 else 'es'} left",
                 "running low on mailboxes")
        if row["live_companies"] and not row["users"]:
            flag("no_users", "warning", row, "create a user",
                 "has companies but no login, so nobody can see them")

    for g in groups.values():
        names = ", ".join(g["tenants"])
        more = g["count"] - len(g["tenants"])
        subject = names + (f" and {more} more" if more > 0 else "")
        out.append({
            "severity": g["severity"], "kind": g["kind"],
            # One tenant keeps its name in `tenant`; a group cannot have one.
            "tenant": g["tenants"][0] if g["count"] == 1 else None,
            "tenants": g["tenants"], "count": g["count"],
            "message": (f"{subject} — {g['phrase']}." if g["count"] == 1
                        else f"{g['count']} tenants — {g['group_phrase']} ({subject})."),
            "action": g["action"],
        })

    failures = queue.get("failures") or []
    # The true count, not len(failures): that list is capped, and an alert
    # that says "20 failed jobs" when there are 200 is worse than none.
    total_failed = int(queue.get("failed_total", len(failures)))
    if total_failed:
        by_stage: dict[str, int] = {}
        for job in failures:
            by_stage[job["stage"]] = by_stage.get(job["stage"], 0) + 1
        worst = ", ".join(f"{n}× {s}" for s, n in
                          sorted(by_stage.items(), key=lambda kv: -kv[1])[:3])
        shown = "" if not queue.get("failures_truncated") else \
            f" — most recent {len(failures)}: "
        detail = f" ({worst})" if worst and not shown else f"{shown}{worst}"
        tenants = int(queue.get("failed_tenants", 0))
        across = f" across {tenants} tenants" if tenants > 1 else ""
        out.append({
            "severity": "critical", "kind": "jobs_failed", "tenant": None,
            "tenants": sorted({j["tenant"] for j in failures})[:3],
            "message": f"{total_failed} failed job"
                       f"{'' if total_failed == 1 else 's'}{across}{detail}.",
            "action": "review",
            "count": total_failed,
        })

    now = time.time()
    stale = []
    for job in queue.get("running") or []:
        beat = job.get("heartbeat_at")
        if beat is None:
            continue
        age = now - beat.timestamp()
        if age > STALE_RUNNING_SECONDS:
            stale.append(f"{job['tenant']}/{job['company_number']} {job['stage']}")
    if stale:
        out.append({
            "severity": "warning", "kind": "jobs_stale", "tenant": None,
            "tenants": [], "count": len(stale),
            "message": f"{len(stale)} job{'' if len(stale) == 1 else 's'} marked "
                       f"running with no recent heartbeat: {', '.join(stale[:3])}.",
            "action": "restart clears these",
        })

    # Configuration gaps are an admin problem, not an operator one: a stage
    # with no credentials fails for every tenant at once.
    try:
        import config
        status = config.inspect()
        if status.stages_blocked:
            out.append({
                "severity": "warning", "kind": "stages_unavailable", "tenant": None,
                "tenants": [], "count": len(status.stages_blocked),
                "message": f"{len(status.stages_blocked)} stage"
                           f"{'' if len(status.stages_blocked) == 1 else 's'} unavailable "
                           f"for every tenant: {', '.join(sorted(status.stages_blocked))}.",
                "action": "check configuration",
            })
    except Exception:  # noqa: BLE001 - never let the console fail on this
        pass

    order = {"critical": 0, "warning": 1}
    out.sort(key=lambda a: (order.get(a["severity"], 9), a["kind"]))
    return out


# ── reads ───────────────────────────────────────────────────────────────────

def _summary_with_artifacts() -> tuple[list[dict], bool]:
    summary = db.admin_tenant_summary()
    budget = [ARTIFACT_SCAN_CAP]
    counts = _artifact_counts(budget)
    for row in summary:
        extra = counts.get(int(row["id"]), {})
        row["apps_built"] = extra.get("apps_built", 0)
        row["released"] = extra.get("released", 0)
    return summary, budget[0] <= 0


@admin_api.route("/api/v2/admin/overview")
@admin_required
def admin_overview():
    """Everything the top of the console needs, in one request."""
    started = time.time()
    summary, capped = _summary_with_artifacts()
    queue = db.admin_job_queue()
    alerts = _alerts(summary, queue)

    return jsonify({
        "totals": {
            "tenants": len(summary),
            "companies": sum(r["companies"] for r in summary),
            "live_companies": sum(r["live_companies"] for r in summary),
            "apps_built": sum(r["apps_built"] for r in summary),
            "released": sum(r["released"] for r in summary),
            "running": len(queue["running"]),
            "queued": len(queue["queued"]),
            # failed_total, not len(failures): that list is capped by the
            # query limit, so the tile read 20 while the alert beside it
            # read the true 31.
            "failed": queue["failed_total"],
        },
        "alerts": alerts,
        # Said plainly rather than left for the reader to assume: the counts
        # above are complete only if the artifact scan was.
        "artifact_scan_complete": not capped,
        "took_ms": round((time.time() - started) * 1000),
    })


#: Tenants returned to the table by default. Alerts already summarise the
#: whole estate, so the table is a working list rather than a census — and an
#: unbounded one rendered a 59,000-pixel page on an instance with 608 tenants.
TENANT_PAGE = 50


@admin_api.route("/api/v2/admin/tenants")
@admin_required
def admin_tenants():
    try:
        limit = max(1, min(int(request.args.get("limit", TENANT_PAGE)), 500))
    except (TypeError, ValueError):
        limit = TENANT_PAGE
    summary, capped = _summary_with_artifacts()
    return jsonify({
        # Ordered by db.admin_tenant_summary so anything in trouble is on the
        # first page; the caller does not have to fetch all of them to find it.
        "tenants": summary[:limit],
        "total": len(summary),
        "truncated": len(summary) > limit,
        "db_derived_stages": list(db.DB_DERIVED_STAGES),
        "artifact_scan_complete": not capped,
    })


@admin_api.route("/api/v2/admin/jobs")
@admin_required
def admin_jobs():
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
    except (TypeError, ValueError):
        limit = 20
    return jsonify(db.admin_job_queue(failed_limit=limit))


@admin_api.route("/api/v2/admin/throughput")
@admin_required
def admin_throughput():
    try:
        days = int(request.args.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    return jsonify({
        "days": db.admin_throughput(days),
        # The jobs table keeps one row per (tenant, company, stage), so a
        # re-run replaces the previous one. Shipped with the data so a
        # reader cannot mistake this for a count of every run.
        "caveat": "Counts stages whose most recent run finished that day. "
                  "A stage re-run later moves, rather than adds to, its day.",
    })


#: Logins returned per page. Bounded for the same reason the tenant table is:
#: an unbounded list is fine until it is not, and the console is where that
#: shows up worst.
USER_PAGE = 100


@admin_api.route("/api/v2/admin/users")
@admin_required
def admin_users():
    try:
        limit = max(1, min(int(request.args.get("limit", USER_PAGE)), 500))
    except (TypeError, ValueError):
        limit = USER_PAGE
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0

    users = db.admin_user_list(limit=limit, offset=offset)
    total = db.admin_user_count()
    return jsonify({
        "users": users,
        "total": total,
        "offset": offset,
        "truncated": offset + len(users) < total,
        "roles": list(db.VALID_ROLES),
    })


# ── writes ──────────────────────────────────────────────────────────────────

@admin_api.route("/api/v2/admin/users/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_set_role(user_id: int):
    from flask import g
    role = str((request.get_json(silent=True) or {}).get("role") or "").strip()
    if role not in db.VALID_ROLES:
        return jsonify({"error": f"role must be one of {list(db.VALID_ROLES)}"}), 400

    # An admin demoting themselves locks the console for everyone if they are
    # the last one. Refuse rather than leave the system unadministrable.
    me = g.user or {}
    if int(user_id) == int(me.get("id", -1)) and role != "admin":
        remaining = [u for u in db.admin_user_list()
                     if u["role"] == "admin" and int(u["id"]) != int(user_id)]
        if not remaining:
            return jsonify({
                "error": "you are the only admin; promote someone else first"
            }), 409

    if not db.set_user_role(user_id, role):
        return jsonify({"error": "no such user"}), 404
    return jsonify({"status": "ok", "user_id": user_id, "role": role})


@admin_api.route("/api/v2/admin/tenants/<slug>/emails", methods=["POST"])
@admin_required
def admin_import_emails(slug: str):
    tenant = db.get_tenant_by_slug(slug)
    if tenant is None:
        return jsonify({"error": "no such tenant"}), 404

    payload = request.get_json(silent=True) or {}
    raw = payload.get("emails")
    if isinstance(raw, str):
        raw = raw.replace(",", "\n").split("\n")
    if not isinstance(raw, list):
        return jsonify({"error": "emails must be a list or a newline-separated string"}), 400

    addresses = [str(e).strip() for e in raw if str(e).strip()]
    rejected = [e for e in addresses if "@" not in e or " " in e]
    if rejected:
        return jsonify({"error": "not an address", "rejected": rejected[:10]}), 400
    if not addresses:
        return jsonify({"error": "no addresses given"}), 400

    added = db.import_emails(int(tenant["id"]), addresses)
    return jsonify({
        "status": "ok", "tenant": slug,
        "added": added, "duplicates": len(addresses) - added,
        "pool": db.email_pool_status(int(tenant["id"])),
    })


# ── page ────────────────────────────────────────────────────────────────────

@admin_api.route("/admin")
@admin_required
def admin_page():
    return send_file(ROOT / "static" / "admin.html")


def init_admin_api(app) -> list[str]:
    """
    Register the blueprint and close the gate behind it.

    Every endpoint this blueprint adds goes into auth.ADMIN_ENDPOINTS, read
    off the url_map rather than from a hand-written list, so a view added
    later cannot be left reachable by an operator because somebody forgot a
    second edit. Returns the names, for the startup log and for tests.
    """
    import auth

    before = {r.endpoint for r in app.url_map.iter_rules()}
    app.register_blueprint(admin_api)
    added = sorted(
        r.endpoint for r in app.url_map.iter_rules() if r.endpoint not in before
    )
    auth.ADMIN_ENDPOINTS.update(added)
    return added
