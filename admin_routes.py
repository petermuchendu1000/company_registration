"""The admin dashboard: who has access, and what they may do.

Access control is NOT repeated here. auth.py's gate refuses anything under
/admin or /api/admin to a caller below admin rank, so every route in this
module is already protected by the time it runs. Re-checking per route
would invite the pattern where one route is added without the check and
nobody notices - the whole point of gating by prefix is that forgetting is
not possible.

What this module does enforce is the difference between admin and
superadmin, because that distinction is per-action rather than per-path:
an admin may approve, suspend and reject people, but only a superadmin may
change roles or delete accounts. Handing out the ability to spend money is
a different act from deciding who may spend it.

The hard invariants - never stranding the system without a superadmin -
live in users.py, not here, because a check in a screen is a suggestion.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template_string, request

import auth
import users

log = logging.getLogger(__name__)
admin = Blueprint("admin", __name__)


def _actor() -> str:
    return auth.actor_name()


def _require_superadmin():
    """None when allowed, else a ready-made error response.

    Asks auth.is_service() explicitly rather than treating "no user object"
    as "must be the service token". Those are different states, and
    conflating them means a request carrying NO identity gets full control.
    Mutation testing produced that exact shape: a session pointing at a
    deleted account fell through the gate with g.user unset, and every
    check here waved it past.
    """
    if auth.is_service():
        return None                      # shared service credential
    u = auth.current_user()
    if u is None:
        return jsonify({"error": "unauthorized"}), 401
    if users.at_least(u["role"], users.ROLE_SUPERADMIN):
        return None
    return jsonify({"error": "Only a superadmin can do that."}), 403


def _self_id() -> int | None:
    u = auth.current_user()
    return u["id"] if u else None


# ── API ─────────────────────────────────────────────────────────────────────
@admin.route("/api/admin/users")
def api_users():
    u = auth.current_user()
    if u is None and not auth.is_service():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "users": users.list_users(),
        "me": {"id": u["id"], "email": u["email"], "role": u["role"]} if u
              else {"id": None, "email": "service-token",
                    "role": users.ROLE_SUPERADMIN},
        "roles": list(users.ROLES),
        "statuses": list(users.STATUSES),
    })


@admin.route("/api/admin/users/<int:user_id>/status", methods=["POST"])
def api_set_status(user_id):
    status = (request.get_json(silent=True) or {}).get("status", "")
    # Suspending yourself is not forbidden by the data layer - it is a
    # legitimate thing for a user to do - but doing it by accident from the
    # admin table is almost never what was meant.
    if user_id == _self_id() and status != users.STATUS_APPROVED:
        return jsonify({"error": "You cannot change your own account's "
                                 "status from here."}), 400
    try:
        return jsonify({"user": users.set_status(user_id, status, _actor())})
    except users.UserError as e:
        return jsonify({"error": str(e)}), 400


@admin.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
def api_set_role(user_id):
    denied = _require_superadmin()
    if denied:
        return denied
    role = (request.get_json(silent=True) or {}).get("role", "")
    if user_id == _self_id():
        return jsonify({"error": "You cannot change your own role."}), 400
    try:
        return jsonify({"user": users.set_role(user_id, role, _actor())})
    except users.UserError as e:
        return jsonify({"error": str(e)}), 400


@admin.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def api_delete(user_id):
    denied = _require_superadmin()
    if denied:
        return denied
    if user_id == _self_id():
        return jsonify({"error": "You cannot delete your own account."}), 400
    try:
        users.delete_user(user_id, _actor())
        return jsonify({"deleted": True})
    except users.UserError as e:
        return jsonify({"error": str(e)}), 400


@admin.route("/api/admin/users/<int:user_id>/password", methods=["POST"])
def api_reset_password(user_id):
    """Set someone's password for them, flagged so they must change it."""
    denied = _require_superadmin()
    if denied:
        return denied
    pw = (request.get_json(silent=True) or {}).get("password", "")
    try:
        # must_change=True: a password an administrator typed is a password
        # two people know. The epoch bump inside set_password also ends any
        # session that account currently has.
        users.set_password(user_id, pw, actor=_actor(), must_change=True)
        return jsonify({"reset": True})
    except users.UserError as e:
        return jsonify({"error": str(e)}), 400


@admin.route("/api/admin/users", methods=["POST"])
def api_create():
    denied = _require_superadmin()
    if denied:
        return denied
    b = request.get_json(silent=True) or {}
    try:
        user = users.create_user(
            b.get("email", ""), b.get("password", ""),
            full_name=b.get("full_name", ""),
            role=b.get("role", users.ROLE_USER),
            status=users.STATUS_APPROVED,
            must_change_password=True,
            actor=_actor())
        return jsonify({"user": user}), 201
    except users.UserError as e:
        return jsonify({"error": str(e)}), 400


@admin.route("/api/admin/audit")
def api_audit():
    return jsonify({"events": users.audit_tail(200)})


# ── Page ────────────────────────────────────────────────────────────────────
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Users — Company Pipeline</title>
<style>
  :root{--bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--border:#30363d;
        --text:#e6edf3;--muted:#8b949e;--blue:#2f81f7;--green:#3fb950;
        --red:#f85149;--yellow:#d29922}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
       color:var(--text);line-height:1.5}
  .topbar{background:var(--surface);border-bottom:1px solid var(--border);
          padding:0 20px;height:48px;display:flex;align-items:center;gap:14px}
  .topbar-logo{font-weight:700;font-size:15px}
  .topbar-logo span{color:var(--blue)}
  .spacer{flex:1}
  .tlink{color:var(--muted);text-decoration:none;font-size:12px;font-weight:600;
         padding:5px 10px;border:1px solid var(--border);border-radius:5px}
  .tlink:hover{color:var(--text);border-color:var(--blue)}
  .wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
  h2{font-size:16px;margin:26px 0 10px;font-weight:600}
  .hint{color:var(--muted);font-size:12.5px;margin-bottom:12px}
  table{width:100%;border-collapse:collapse;font-size:13px;
        background:var(--surface);border:1px solid var(--border);border-radius:8px;
        overflow:hidden}
  th,td{text-align:left;padding:9px 11px;border-bottom:1px solid var(--border);
        vertical-align:middle}
  th{color:var(--muted);font-size:11px;text-transform:uppercase;
     letter-spacing:.4px;font-weight:600;background:var(--surface2)}
  tr:last-child td{border-bottom:none}
  tr.pending{background:rgba(210,153,34,.07)}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
        font-weight:600;border:1px solid var(--border)}
  .s-approved{color:var(--green);border-color:var(--green)}
  .s-pending{color:var(--yellow);border-color:var(--yellow)}
  .s-suspended,.s-rejected{color:var(--red);border-color:var(--red)}
  .r-superadmin{color:var(--blue);border-color:var(--blue)}
  button{font-family:inherit;font-size:12px;font-weight:600;padding:4px 9px;
         border-radius:5px;border:1px solid var(--border);background:var(--surface2);
         color:var(--text);cursor:pointer;margin-right:4px}
  button:hover{border-color:var(--blue)}
  button.go{background:var(--green);border-color:var(--green);color:#06230f}
  button.no{background:transparent;border-color:var(--red);color:#ff8b82}
  button:disabled{opacity:.35;cursor:not-allowed}
  select{font-family:inherit;font-size:12px;background:var(--surface2);
         color:var(--text);border:1px solid var(--border);border-radius:5px;
         padding:3px 6px}
  .muted{color:var(--muted);font-size:12px}
  #toast{position:fixed;right:18px;bottom:18px;max-width:420px}
  .t{background:var(--surface);border:1px solid var(--border);border-left-width:3px;
     border-radius:6px;padding:10px 13px;font-size:13px;margin-top:8px}
  .t.err{border-left-color:var(--red)} .t.ok{border-left-color:var(--green)}
</style></head>
<body>
<div class="topbar">
  <div class="topbar-logo">Company <span>Pipeline</span></div>
  <div class="spacer"></div>
  <span class="muted" id="whoami"></span>
  <a class="tlink" href="/">Dashboard</a>
  <a class="tlink" href="/change-password">Change password</a>
  <a class="tlink" href="/logout">Sign out</a>
</div>
<div class="wrap">
  <h2>Waiting for approval <span class="muted" id="pcount"></span></h2>
  <div class="hint">Nobody can sign in until you approve them here.</div>
  <table><thead><tr><th>Name</th><th>Email</th><th>Requested</th><th>Decision</th></tr></thead>
    <tbody id="pending"></tbody></table>

  <h2>Accounts</h2>
  <div class="hint" id="roles-hint"></div>
  <table><thead><tr>
    <th>Name</th><th>Email</th><th>Role</th><th>Status</th>
    <th>Last signed in</th><th>Actions</th></tr></thead>
    <tbody id="users"></tbody></table>

  <h2>Access log</h2>
  <div class="hint">Every approval, suspension, role change and password reset.</div>
  <table><thead><tr><th>When</th><th>Who</th><th>Did</th><th>To</th><th>Detail</th></tr></thead>
    <tbody id="audit"></tbody></table>
</div>
<div id="toast"></div>
<script>
const esc = s => String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const when = s => s ? new Date(s).toLocaleString() : "\\u2014";
let ME = {role:"user", id:null};

function toast(msg, kind){
  const d=document.createElement("div"); d.className="t "+(kind||"ok"); d.textContent=msg;
  document.getElementById("toast").appendChild(d); setTimeout(()=>d.remove(),5000);
}
async function api(method, path, body){
  const o={method, headers:{}};
  if(body!==undefined){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body);}
  const r=await fetch(path,o); let d={}; try{d=await r.json();}catch(e){}
  if(!r.ok) throw new Error(d.error || ("HTTP "+r.status));
  return d;
}
async function load(){
  let d;
  try { d = await api("GET","/api/admin/users"); }
  catch(e){ toast("Could not load accounts: "+e.message,"err"); return; }
  ME = d.me;
  document.getElementById("whoami").textContent = ME.email+" \\u00b7 "+ME.role;
  const sa = ME.role === "superadmin";
  document.getElementById("roles-hint").textContent = sa
    ? "You are a superadmin: you can change roles, reset passwords and delete accounts."
    : "You can approve and suspend people. Only a superadmin can change roles or delete accounts.";

  const pend = d.users.filter(u=>u.status==="pending");
  document.getElementById("pcount").textContent = pend.length ? "("+pend.length+")" : "(none)";
  document.getElementById("pending").innerHTML = pend.length ? pend.map(u=>`
    <tr class="pending"><td>${esc(u.full_name)||"\\u2014"}</td><td>${esc(u.email)}</td>
    <td>${when(u.created_at)}</td><td>
      <button class="go" onclick="setStatus(${u.id},'approved')">Approve</button>
      <button class="no" onclick="setStatus(${u.id},'rejected')">Reject</button>
    </td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Nobody is waiting.</td></tr>`;

  document.getElementById("users").innerHTML = d.users.map(u=>{
    const self = u.id === ME.id;
    const roleSel = sa && !self
      ? `<select onchange="setRole(${u.id},this.value)">`+
        d.roles.map(r=>`<option value="${r}" ${r===u.role?"selected":""}>${r}</option>`).join("")+
        `</select>`
      : `<span class="pill r-${esc(u.role)}">${esc(u.role)}</span>`;
    const acts = [];
    if(!self){
      if(u.status==="approved") acts.push(`<button class="no" onclick="setStatus(${u.id},'suspended')">Suspend</button>`);
      else acts.push(`<button class="go" onclick="setStatus(${u.id},'approved')">Approve</button>`);
      if(sa){
        acts.push(`<button onclick="resetPw(${u.id},'${esc(u.email)}')">Reset password</button>`);
        acts.push(`<button class="no" onclick="del(${u.id},'${esc(u.email)}')">Delete</button>`);
      }
    } else acts.push(`<span class="muted">that's you</span>`);
    return `<tr><td>${esc(u.full_name)||"\\u2014"}</td><td>${esc(u.email)}
      ${u.must_change_password?' <span class="pill s-pending">must change password</span>':""}</td>
      <td>${roleSel}</td>
      <td><span class="pill s-${esc(u.status)}">${esc(u.status)}</span></td>
      <td class="muted">${when(u.last_login_at)}</td><td>${acts.join("")}</td></tr>`;
  }).join("");

  try{
    const a = await api("GET","/api/admin/audit");
    document.getElementById("audit").innerHTML = a.events.length ? a.events.map(e=>`
      <tr><td class="muted">${when(e.at)}</td><td>${esc(e.actor)}</td>
      <td>${esc(e.action)}</td><td>${esc(e.subject)}</td>
      <td class="muted">${esc(e.detail)}</td></tr>`).join("")
      : `<tr><td colspan="5" class="muted">Nothing yet.</td></tr>`;
  }catch(e){ /* the table is informational; its failure must not hide the page */ }
}
async function setStatus(id,status){
  try{ await api("POST",`/api/admin/users/${id}/status`,{status});
       toast("Account "+status+".","ok"); load(); }
  catch(e){ toast(e.message,"err"); }
}
async function setRole(id,role){
  try{ await api("POST",`/api/admin/users/${id}/role`,{role});
       toast("Role changed to "+role+".","ok"); load(); }
  catch(e){ toast(e.message,"err"); load(); }
}
async function del(id,email){
  if(!confirm("Delete "+email+"? This cannot be undone.")) return;
  try{ await api("DELETE",`/api/admin/users/${id}`); toast("Deleted.","ok"); load(); }
  catch(e){ toast(e.message,"err"); }
}
async function resetPw(id,email){
  const pw = prompt("New password for "+email+" (at least 12 characters).\\n"+
                    "They will be made to change it when they next sign in.");
  if(!pw) return;
  try{ await api("POST",`/api/admin/users/${id}/password`,{password:pw});
       toast("Password reset. Their current sessions were ended.","ok"); load(); }
  catch(e){ toast(e.message,"err"); }
}
load();
</script></body></html>"""


@admin.route("/admin")
def admin_page():
    return render_template_string(_PAGE)
