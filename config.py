"""
Every setting this system reads, in one place.

Thirty environment variables are read across fourteen modules, each with its
own default spelled inline and no record anywhere of which ones a given stage
actually needs. The result is that a half-configured deployment looks healthy
until a stage runs: the dashboard comes up, a company is added, and the first
Namecheap call fails with a bare KeyError an hour later.

This module is the contract. It does not replace those reads — migrating
thirty call sites would be churn for its own sake, and some of them live in
modules this project does not maintain. What it does is declare them, say
which stage each one gates, and refuse to start when something required is
missing, so a misconfiguration is a startup error with a name attached
rather than a stage failure later.

    python config.py check          what is configured, and what it unlocks
    python config.py check --strict non-zero exit if any stage is unavailable
    python config.py template       regenerate .env.example

`check` prints no secret values, only whether each is set, so it is safe to
run in a deploy log or paste into an issue.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: Pipeline stages, in the order the dashboard runs them.
STAGES = ["details", "duns", "certificate", "domain",
          "email", "director_id", "app", "publish"]


@dataclass(frozen=True)
class Setting:
    name: str
    summary: str
    default: str | None = None
    secret: bool = False
    #: Stages that cannot run without it. Empty means it only tunes behaviour.
    gates: tuple[str, ...] = ()
    #: True when the process should refuse to start without it.
    required: bool = False
    #: Older names still honoured, in order.
    aliases: tuple[str, ...] = ()
    #: Shown in .env.example when there is no safe default to suggest.
    example: str = ""
    group: str = "General"


SETTINGS: list[Setting] = [
    # ── core ────────────────────────────────────────────────────────────────
    Setting(
        "DATABASE_URL", required=True, secret=True, group="Core",
        aliases=("SUPABASE_DB_URL",),
        summary="PostgreSQL connection string. The source of truth for "
                "tenants, companies, the job registry and the email pool.",
        example="postgresql://appuser:password@db:5432/company_factory",
    ),
    Setting(
        "FLASK_SECRET_KEY", required=True, secret=True, group="Core",
        summary="Signs session cookies. Changing it logs everyone out; "
                "sharing it between deployments lets one forge the other's "
                "sessions. Generate with: python -c "
                "'import secrets;print(secrets.token_urlsafe(48))'",
        example="<48 random url-safe characters>",
    ),
    Setting(
        "SINGLE_TENANT_SLUG", group="Core",
        summary="Resolves a tenant whenever none is bound, for a "
                "single-client deployment. Leave unset when more than one "
                "tenant uses this instance: it would silently answer for a "
                "request that failed to bind one.",
        example="acme",
    ),
    Setting(
        "PIPELINE_OUTPUT_DIR", group="Core",
        default=str(ROOT / "pipeline_output"),
        # The default is this checkout, which is right for a developer and
        # wrong for a server; the example is what a deployment should use.
        example="/var/lib/company-factory/pipeline_output",
        summary="Where generated files live: certificates, IDs, APKs, AABs. "
                "Point it at a data volume — it grows with every build and "
                "must survive a redeploy.",
    ),

    # ── web ─────────────────────────────────────────────────────────────────
    Setting("HOST", default="127.0.0.1", group="Web",
            summary="Interface to bind. Behind a reverse proxy this stays on "
                    "loopback; in a container it must be 0.0.0.0."),
    Setting("PORT", default="5000", group="Web", summary="Port to bind."),
    Setting("FLASK_DEBUG", default="0", group="Web",
            summary="1 enables the Werkzeug console, which is remote code "
                    "execution for anyone who can reach the port. Local only; "
                    "the app refuses it on a non-loopback host."),
    Setting("SESSION_COOKIE_SECURE", default="0", group="Web",
            summary="1 marks the session cookie Secure. Set it to 1 wherever "
                    "the site is served over HTTPS."),
    Setting("SESSION_HOURS", default="12", group="Web",
            summary="How long a signed-in session lasts."),
    Setting("LOGIN_MAX_ATTEMPTS", default="10", group="Web",
            summary="Failed sign-ins allowed per window before lockout."),
    Setting("LOGIN_WINDOW_SECONDS", default="300", group="Web",
            summary="The lockout window, in seconds."),
    Setting("MAX_UPLOAD_MB", default="25", group="Web",
            summary="Largest accepted upload."),

    # ── stages ──────────────────────────────────────────────────────────────
    Setting(
        "COMPANIES_HOUSE_API_KEY", secret=True, group="Companies House",
        gates=("details", "certificate"),
        summary="Companies House REST key. Free, from "
                "developer.company-information.service.gov.uk. Also used to "
                "backfill a director's date of birth for the ID stage.",
    ),
    Setting("NAMECHEAP_API_KEY", secret=True, group="Namecheap",
            gates=("domain", "email"), summary="Namecheap API key."),
    Setting("NAMECHEAP_API_USER", group="Namecheap", gates=("domain", "email"),
            summary="Namecheap API user — usually the account name."),
    Setting("NAMECHEAP_USERNAME", group="Namecheap", gates=("domain", "email"),
            summary="Namecheap account username."),
    Setting("NAMECHEAP_CLIENT_IP", group="Namecheap", gates=("domain", "email"),
            summary="The public IP allow-listed in Namecheap. A call from any "
                    "other address is refused, so this must be the VPS's "
                    "egress IP, not a developer laptop's.",
            example="203.0.113.10"),
    Setting("NAMECHEAP_SANDBOX", default="0", group="Namecheap",
            summary="1 routes every call to sandbox.namecheap.com. Registers "
                    "nothing and spends nothing — use it for any test run."),
    Setting("NAMECHEAP_MIN_BALANCE", default="15.0", group="Namecheap",
            summary="Registration is refused below this account balance, so a "
                    "batch cannot drain the account."),
    Setting("NAMECHEAP_DEFAULT_ADDRESS_ID", group="Namecheap",
            summary="Registrant address ID used for new registrations. "
                    "Discovered from the account when unset."),
    Setting("NAMECHEAP_REFERENCE_DOMAIN", group="Namecheap",
            summary="An existing domain to copy contact details from."),
    Setting("XBINDER_EMAIL", group="Director ID", gates=("director_id",),
            summary="XBINDER account. Without both of these the director_id "
                    "stage logs SKIP and returns, which is harmless: it does "
                    "not stop the stages after it."),
    Setting("XBINDER_PASSWORD", secret=True, group="Director ID",
            gates=("director_id",), summary="XBINDER password."),

    # ── build and publish ───────────────────────────────────────────────────
    Setting("ANDROID_SDK_ROOT", group="Build", gates=("app",),
            aliases=("ANDROID_HOME",),
            summary="Android SDK location. Needs platform-tools, a build-tools "
                    "release and the compile platform.",
            example="/opt/android-sdk"),
    Setting("JAVA_HOME", group="Build", gates=("app",),
            summary="JDK 17 or newer, for Gradle.",
            example="/usr/lib/jvm/java-21-openjdk-amd64"),
    Setting("MAX_CONCURRENT_BUILDS", default="1", group="Build",
            summary="Gradle builds allowed at once. Each wants a GB or more of "
                    "heap, so this is the main lever on memory. The limit is "
                    "held in-process, so it only holds within one app process."),
    Setting("PLAY_SERVICE_ACCOUNTS_DIR", group="Publish", gates=("publish",),
            summary="Directory of per-company Google Play service account "
                    "JSON, named {company_number}.json. Mount it read-only; "
                    "these keys can publish to the store.",
            example="/run/secrets/play_service_accounts"),
    Setting("PLAY_DATA_SAFETY_TEMPLATE", group="Publish",
            summary="Data Safety CSV applied to each listing. Without it the "
                    "Play Console section is left for a human."),
]

BY_NAME: dict[str, Setting] = {s.name: s for s in SETTINGS}

_LOADED = False


# ── reading ──────────────────────────────────────────────────────────────────

def load_env(path: str | os.PathLike | None = None) -> None:
    """Load .env once. Real environment variables always win."""
    global _LOADED
    if _LOADED and path is None:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOADED = True
        return
    load_dotenv(path or (ROOT / ".env"), override=False)
    _LOADED = True


def raw(name: str) -> str:
    """The value for a setting, honouring aliases then the default."""
    setting = BY_NAME.get(name)
    names = (name,) + (setting.aliases if setting else ())
    for candidate in names:
        value = os.environ.get(candidate)
        if value is not None and value.strip():
            return value.strip()
    return (setting.default if setting and setting.default else "") or ""


def is_set(name: str) -> bool:
    return bool(raw(name))


def flag(name: str) -> bool:
    return raw(name).lower() in ("1", "true", "yes", "on")


def integer(name: str) -> int:
    value = raw(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        default = (BY_NAME.get(name).default if BY_NAME.get(name) else "0") or "0"
        return int(default)


def database_url() -> str:
    """The one place the DATABASE_URL/SUPABASE_DB_URL alias is resolved."""
    return raw("DATABASE_URL")


# ── validating ───────────────────────────────────────────────────────────────

@dataclass
class Status:
    missing_required: list[str] = field(default_factory=list)
    stages_ready: list[str] = field(default_factory=list)
    stages_blocked: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_start(self) -> bool:
        return not self.missing_required


def inspect() -> Status:
    load_env()
    st = Status()
    st.missing_required = [s.name for s in SETTINGS if s.required and not is_set(s.name)]

    for stage in STAGES:
        needed = [s.name for s in SETTINGS if stage in s.gates and not is_set(s.name)]
        if needed:
            st.stages_blocked[stage] = needed
        else:
            st.stages_ready.append(stage)

    # Things that are configured, but configured dangerously.
    if flag("FLASK_DEBUG"):
        st.warnings.append(
            "FLASK_DEBUG is on — the Werkzeug console is remote code execution "
            "for anyone who can reach the port. Never set it on a public host.")
    if not flag("SESSION_COOKIE_SECURE"):
        st.warnings.append(
            "SESSION_COOKIE_SECURE is off — the session cookie will be sent "
            "over plain HTTP. Set it to 1 once TLS is in front.")
    if is_set("NAMECHEAP_API_KEY") and not flag("NAMECHEAP_SANDBOX"):
        st.warnings.append(
            "NAMECHEAP_SANDBOX is off — the domain stage will register real "
            "domains and spend real money.")
    if is_set("SINGLE_TENANT_SLUG"):
        st.warnings.append(
            f"SINGLE_TENANT_SLUG={raw('SINGLE_TENANT_SLUG')!r} — any code path "
            "that fails to bind a tenant silently gets this one. Unset it on a "
            "multi-tenant instance.")
    secret = raw("FLASK_SECRET_KEY")
    if secret and len(secret) < 32:
        st.warnings.append(
            f"FLASK_SECRET_KEY is only {len(secret)} characters; use at least 32.")
    out = raw("PIPELINE_OUTPUT_DIR")
    if out and not Path(out).parent.exists():
        st.warnings.append(f"PIPELINE_OUTPUT_DIR={out} — its parent does not exist.")
    return st


def require_startup_settings() -> None:
    """Fail fast, by name. Called from app startup and the CLI entrypoint."""
    st = inspect()
    if st.can_start:
        return
    names = ", ".join(st.missing_required)
    raise SystemExit(
        f"Refusing to start: {names} not set.\n"
        f"Run `python config.py check` for what each one is, "
        f"or copy .env.example to .env and fill it in."
    )


# ── reporting ────────────────────────────────────────────────────────────────

_TICK, _CROSS, _DOT = "OK ", "-- ", ".. "


def report(st: Status | None = None) -> str:
    st = st or inspect()
    lines: list[str] = ["", "  Configuration", "  " + "─" * 58]

    group = None
    for s in SETTINGS:
        if s.group != group:
            group = s.group
            lines.append(f"\n  {group}")
        mark = _TICK if is_set(s.name) else (_CROSS if s.required or s.gates else _DOT)
        shown = "set" if s.secret and is_set(s.name) else (raw(s.name) or "unset")
        if s.secret and is_set(s.name):
            shown = f"set ({len(raw(s.name))} chars)"
        note = ""
        if not is_set(s.name) and s.gates:
            note = f"  → blocks {', '.join(s.gates)}"
        elif s.required and not is_set(s.name):
            note = "  → required"
        lines.append(f"    {mark}{s.name:<30} {shown}{note}")

    lines += ["", "  Stages", "  " + "─" * 58]
    for stage in STAGES:
        if stage in st.stages_blocked:
            lines.append(f"    {_CROSS}{stage:<14} needs {', '.join(st.stages_blocked[stage])}")
        else:
            lines.append(f"    {_TICK}{stage:<14} ready")

    if st.warnings:
        lines += ["", "  Warnings", "  " + "─" * 58]
        lines += [f"    ! {w}" for w in st.warnings]

    lines += ["", "  " + "─" * 58]
    if st.missing_required:
        lines.append(f"  CANNOT START — missing {', '.join(st.missing_required)}")
    else:
        lines.append(f"  Can start. {len(st.stages_ready)}/{len(STAGES)} stages ready.")
    lines.append("")
    return "\n".join(lines)


def template() -> str:
    """The contents of .env.example, generated from the schema."""
    out = [
        "# Configuration for the company app factory.",
        "#",
        "# Copy to .env and fill in. Real environment variables win over this",
        "# file, so a container can override any of it.",
        "#",
        "# Check what you have with:  python config.py check",
        "",
    ]
    group = None
    for s in SETTINGS:
        if s.group != group:
            group = s.group
            out += [f"# {'=' * 70}", f"# {group}", f"# {'=' * 70}", ""]
        for line in _wrap(s.summary, 74):
            out.append(f"# {line}")
        if s.gates:
            out.append(f"# Required for: {', '.join(s.gates)}")
        if s.required:
            out.append("# REQUIRED — the app will not start without it.")
        value = s.example or s.default or ""
        # Only the settings with no working default are left live. Everything
        # else ships commented out, so copying this file to .env cannot
        # silently switch on something like SINGLE_TENANT_SLUG.
        prefix = "" if s.required else "# "
        out += [f"{prefix}{s.name}={value}", ""]
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    words, line, lines = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="report what is configured")
    c.add_argument("--strict", action="store_true",
                   help="exit non-zero if any stage is unavailable")
    sub.add_parser("template", help="print .env.example")
    args = p.parse_args()

    if args.cmd == "template":
        print(template())
        return 0

    st = inspect()
    print(report(st))
    if not st.can_start:
        return 2
    if getattr(args, "strict", False) and st.stages_blocked:
        print(f"  strict: {len(st.stages_blocked)} stage(s) unavailable\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
