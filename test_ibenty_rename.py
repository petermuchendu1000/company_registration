#!/usr/bin/env python3
"""Checks the xbinder -> ibenty rename, including what it must NOT break.

    python3 test_ibenty_rename.py

Hermetic: no network, no browser, no credentials. playwright is stubbed on
import because ibenty_auto imports it at module scope and nothing here
drives a browser.

The rename is mostly cosmetic, with one part that is not. deploy's own
set_xbinder_env.sh writes XBINDER_EMAIL / XBINDER_PASSWORD, so a server set
up with it holds those names. Reading only the new names would turn a
configured box into one that prints "SKIP - set IBENTY_EMAIL ..." and
quietly does nothing - on the single stage that handles a person's identity
documents, and with no error anywhere to say why. So the old names are
still read, second, and saying so.

The same applies to the profile key. duns_requests.json is operator-owned:
it is gitignored, nothing in this application writes these keys
(duns_automation.py has no xbinder or ibenty reference at all), so a file
written by hand before the rename can still say "xbinder_profile".
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")


def _stub_playwright() -> None:
    if "playwright" in sys.modules:
        return
    pw = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    class _TimeoutError(Exception):
        pass

    def _sync_playwright(*a, **k):  # pragma: no cover - never called here
        raise RuntimeError("playwright is stubbed; this suite drives no browser")

    sync_api.TimeoutError = _TimeoutError
    sync_api.sync_playwright = _sync_playwright
    pw.sync_api = sync_api
    sys.modules["playwright"] = pw
    sys.modules["playwright.sync_api"] = sync_api


_stub_playwright()

import ibenty_auto  # noqa: E402
import pipeline  # noqa: E402


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
        return bool(cond)

    def report(self) -> int:
        print()
        print(f"{self.passed} passed / {len(self.failed)} failed")
        for f in self.failed:
            print(f"  failed: {f}")
        return 1 if self.failed else 0


CRED_VARS = ("IBENTY_EMAIL", "IBENTY_PASSWORD",
             "XBINDER_EMAIL", "XBINDER_PASSWORD")


def _creds(**env):
    """Call the real _ibenty_credentials with exactly this environment."""
    saved = {k: os.environ.get(k) for k in CRED_VARS}
    buf = io.StringIO()
    try:
        for k in CRED_VARS:
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        with redirect_stdout(buf):
            got = pipeline._ibenty_credentials()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return got, buf.getvalue()


# ── the module itself ───────────────────────────────────────────────────
def check_module(r: Results) -> None:
    print("\n-- the module is named for the service it talks to --")
    r.check("ibenty_auto.py exists", (REPO / "ibenty_auto.py").is_file())
    r.check("xbinder_auto.py is gone", not (REPO / "xbinder_auto.py").exists(),
            "two modules would mean two copies free to drift")
    r.check("it still points at ibenty",
            ibenty_auto.SITE == "https://ibenty.com"
            and ibenty_auto.API_BASE == "https://apis.ibenty.com/api",
            f"SITE={ibenty_auto.SITE!r} API_BASE={ibenty_auto.API_BASE!r}")

    src = (REPO / "pipeline.py").read_text(encoding="utf-8")
    r.check("pipeline imports ibenty_auto", "from ibenty_auto import" in src)
    r.check("...and not the old name", "from xbinder_auto import" not in src)

    # Every public name stage 4 pulls in must still be there, or the rename
    # moved the file and broke the import it was supposed to fix.
    for fn in ("login", "create_bind", "search_bind", "download_id_via_ui",
               "split_combined_id_image", "load_director_profile"):
        r.check(f"ibenty_auto.{fn} is importable", hasattr(ibenty_auto, fn))


# ── credentials: the part that can silently break a live box ────────────
def check_credentials(r: Results) -> None:
    print("\n-- the current variable names are preferred --")
    got, out = _creds(IBENTY_EMAIL="a@b.c", IBENTY_PASSWORD="pw")
    r.check("IBENTY_* are used", got == ("a@b.c", "pw"), f"got {got!r}")
    r.check("...with no deprecation notice", "NOTE" not in out, f"out={out!r}")
    r.check("...and no SKIP", "SKIP" not in out, f"out={out!r}")

    print("\n-- a .env written before the rename still works --")
    got, out = _creds(XBINDER_EMAIL="old@b.c", XBINDER_PASSWORD="oldpw")
    r.check("XBINDER_* are still read", got == ("old@b.c", "oldpw"),
            "a server configured by the old deploy script must not silently "
            f"stop doing stage 4. got {got!r}")
    r.check("...and the fallback says so", "NOTE" in out, f"out={out!r}")
    r.check("...naming both old variables",
            "XBINDER_EMAIL" in out and "XBINDER_PASSWORD" in out, f"out={out!r}")
    r.check("...and naming the replacement",
            "IBENTY_EMAIL" in out and "IBENTY_PASSWORD" in out, f"out={out!r}")
    r.check("...and pointing at the script that migrates them",
            "set_ibenty_env.sh" in out, f"out={out!r}")

    print("\n-- the new names win when both are present --")
    got, out = _creds(IBENTY_EMAIL="new@b.c", IBENTY_PASSWORD="newpw",
                      XBINDER_EMAIL="old@b.c", XBINDER_PASSWORD="oldpw")
    r.check("IBENTY_* beat XBINDER_*", got == ("new@b.c", "newpw"),
            f"got {got!r}")
    r.check("...quietly, since nothing is deprecated in use",
            "NOTE" not in out, f"out={out!r}")

    print("\n-- nothing configured, and half configured --")
    got, out = _creds()
    r.check("no credentials gives ('', '')", got == ("", ""), f"got {got!r}")
    r.check("...and says which names to set",
            "IBENTY_EMAIL" in out and "IBENTY_PASSWORD" in out, f"out={out!r}")
    r.check("...as a SKIP", "SKIP" in out, f"out={out!r}")

    # "set both" is unhelpful when one of the two is already there, and a
    # half-configured box is the likeliest way to get here.
    for present, missing in (("IBENTY_EMAIL", "IBENTY_PASSWORD"),
                             ("IBENTY_PASSWORD", "IBENTY_EMAIL"),
                             ("XBINDER_EMAIL", "XBINDER_PASSWORD")):
        got, out = _creds(**{present: "x"})
        r.check(f"only {present} set -> refused", got == ("", ""), f"got {got!r}")
        r.check(f"...and the message names {present}", present in out,
                f"out={out!r}")

    # Whitespace-only is not configuration - per variable, because blanking
    # both at once passes even when only one of the two is stripped.
    got, out = _creds(IBENTY_EMAIL="   ", IBENTY_PASSWORD="   ")
    r.check("whitespace-only credentials are refused", got == ("", ""),
            f"got {got!r}")
    for var, other in (("IBENTY_EMAIL", "IBENTY_PASSWORD"),
                       ("IBENTY_PASSWORD", "IBENTY_EMAIL"),
                       ("XBINDER_EMAIL", "XBINDER_PASSWORD"),
                       ("XBINDER_PASSWORD", "XBINDER_EMAIL")):
        got, out = _creds(**{var: "   ", other: "real"})
        r.check(f"whitespace-only {var} alone is refused", got == ("", ""),
                f"a value of spaces is not a credential. got {got!r}")

    # An empty IBENTY_ pair must not shadow a working XBINDER_ pair.
    got, out = _creds(IBENTY_EMAIL="", IBENTY_PASSWORD="",
                      XBINDER_EMAIL="old@b.c", XBINDER_PASSWORD="oldpw")
    r.check("empty new names fall through to the old ones",
            got == ("old@b.c", "oldpw"),
            f"an empty IBENTY_EMAIL must not mask a configured box. got {got!r}")


# ── the profile key, read from an operator-owned file ───────────────────
def check_profile_key(r: Results) -> None:
    print("\n-- the director profile accepts the old key name --")
    base = {"company_number": "00000001",
            "company_data": {"primary_director": {}}}
    profile = {"fname": "Jane", "lname": "Doe"}

    for key in ("ibenty_profile", "ibenty", "xbinder_profile", "xbinder"):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "duns_requests.json"
            f.write_text(json.dumps({"00000001": {**base, key: profile}}),
                         encoding="utf-8")
            try:
                got = ibenty_auto.load_director_profile(f, "00000001")
                ok = got.get("fname") == "Jane" and got.get("lname") == "Doe"
                err = ""
            except Exception as e:                  # noqa: BLE001 - reported
                ok, err = False, f"{type(e).__name__}: {e}"
            r.check(f"{key!r} is read", ok, err or f"got {got!r}")

    # The current name must win, or the fallback is not a fallback.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "duns_requests.json"
        f.write_text(json.dumps({"00000001": {
            **base,
            "ibenty_profile": {"fname": "New", "lname": "Name"},
            "xbinder_profile": {"fname": "Old", "lname": "Name"}}}),
            encoding="utf-8")
        got = ibenty_auto.load_director_profile(f, "00000001")
        r.check("ibenty_profile wins over xbinder_profile",
                got.get("fname") == "New", f"got fname={got.get('fname')!r}")

    # A profile with no usable names must still be an error, or the checks
    # above would pass against a function that returns anything at all.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "duns_requests.json"
        f.write_text(json.dumps({"00000001": dict(base)}), encoding="utf-8")
        try:
            ibenty_auto.load_director_profile(f, "00000001")
            raised, msg = False, ""
        except Exception as e:                      # noqa: BLE001 - reported
            raised, msg = True, str(e)
        r.check("a profile with no names is still rejected", raised,
                "if this passes, the checks above prove nothing")
        r.check("...and the message names the current key",
                "ibenty_profile" in msg, f"message was {msg!r}")


# ── operator-facing text ────────────────────────────────────────────────
def check_user_facing_text(r: Results) -> None:
    print("\n-- what an operator reads says ibenty --")
    FILES = ("pipeline.py", "ibenty_auto.py", "api_v2.py", "run_pipeline.py",
             "static/index.html", "DEPLOYMENT.md")
    # Deliberate survivors: the back-compat path, and the notes recording
    # what the service used to be called. Anything else is a leftover.
    ALLOWED = (
        "XBINDER_EMAIL", "XBINDER_PASSWORD",          # read as a fallback
        "xbinder_profile", '"xbinder"',               # read as a fallback
        "xbinder.net",                                # the old host, in a note
        "The service was called xbinder",
        "and \"xbinder\" are what the service was called",
        "profile block. \"ibenty_profile\" is the current name; \"xbinder_profile\"",
    )
    for fn in FILES:
        leftovers = []
        for i, line in enumerate((REPO / fn).read_text(encoding="utf-8")
                                 .splitlines(), 1):
            if "xbinder" not in line.lower():
                continue
            if any(a in line for a in ALLOWED):
                continue
            leftovers.append(f"{fn}:{i}: {line.strip()[:70]}")
        r.check(f"{fn} has no stray xbinder naming", not leftovers,
                "; ".join(leftovers))

    # And the stage banner, which is the first thing printed.
    src = (REPO / "pipeline.py").read_text(encoding="utf-8")
    r.check("the stage 4 banner says ibenty",
            'STAGE 4 — Director ID (ibenty)' in src)
    r.check("the login messages say ibenty",
            '"  ibenty login: OK"' in src and "ibenty login FAILED" in src)


# ── the deploy script ───────────────────────────────────────────────────
def _run_deploy(tmp: Path, py_exit: int, env_body: str):
    """Run the real deploy script in a sandbox with a stub interpreter.

    APP_DIR and PY are overridable precisely so this can happen. Checking
    the script by grepping its source is how a broken one shipped: the
    source contained "rc=$?" and the check passed, while under `set -e` the
    shell terminated at the failing command and that line never ran.
    """
    (tmp / ".env").write_text(env_body, encoding="utf-8")
    argv_log = tmp / "argv.txt"
    stub = tmp / "stubpy"
    stub.write_text(f'#!/bin/sh\nprintf "%s" "$*" > {argv_log}\nexit {py_exit}\n',
                    encoding="utf-8")
    stub.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(REPO / "deploy" / "set_ibenty_env.sh"), "a@b.c", "pw"],
        env={**os.environ, "APP_DIR": str(tmp), "PY": str(stub)},
        capture_output=True, text=True, timeout=120)
    argv = argv_log.read_text(encoding="utf-8").strip() if argv_log.exists() else ""
    return proc.returncode, proc.stdout + proc.stderr, argv


def check_deploy_script(r: Results) -> None:
    print("\n-- deploy/set_ibenty_env.sh --")
    p = REPO / "deploy" / "set_ibenty_env.sh"
    r.check("the script exists", p.is_file())
    if not p.is_file():
        return
    r.check("it is executable", bool(p.stat().st_mode & stat.S_IXUSR),
            f"mode {oct(stat.S_IMODE(p.stat().st_mode))}")
    src = p.read_text(encoding="utf-8")
    r.check("no credential is baked into the script",
            "@" not in src.split("say()")[0].replace("#!/usr/bin/env bash", ""),
            "a credential in the repository is published when it is pushed")

    ENV_BEFORE = ("DATABASE_URL=postgresql://app:s3cret@db/company\n"
                  "FLASK_SECRET_KEY=keep-me\n"
                  "XBINDER_EMAIL=old@example.com\n"
                  "XBINDER_PASSWORD=oldpw\n")

    print("\n   the verification step actually runs")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out, argv = _run_deploy(tmp, 0, ENV_BEFORE)
        # load_dotenv() walks back through the CALLER's frames to find .env,
        # and a script piped in on stdin has no caller frame - it dies on
        # `assert frame.f_back is not None`. The interpreter must therefore
        # be handed a real file, which is also how the app loads it.
        r.check("the interpreter is given a real file, not stdin",
                argv.endswith(".py") and argv != "-",
                f"the verification was invoked as {argv!r}; '-' means stdin, "
                f"which makes load_dotenv() raise AssertionError")
        r.check("...inside APP_DIR, where .env is",
                argv.startswith(str(tmp)), f"invoked with {argv!r}")
        r.check("a successful verification exits 0", rc == 0, f"rc={rc}\n{out}")
        r.check("...and the temp verification file is cleaned up",
                not list(tmp.glob(".verify_ibenty*")),
                f"left behind: {[x.name for x in tmp.glob('.verify_ibenty*')]}")

        body = (tmp / ".env").read_text(encoding="utf-8")
        r.check("the current names are written",
                "IBENTY_EMAIL=a@b.c" in body and "IBENTY_PASSWORD=pw" in body,
                f"got {body!r}")
        r.check("the superseded names are removed",
                "XBINDER_EMAIL=" not in body and "XBINDER_PASSWORD=" not in body,
                f"two credentials that can disagree. got {body!r}")
        r.check("every other key survives",
                "DATABASE_URL=postgresql://app:s3cret@db/company" in body
                and "FLASK_SECRET_KEY=keep-me" in body, f"got {body!r}")
        r.check("...and .env is left at mode 600",
                stat.S_IMODE((tmp / ".env").stat().st_mode) == 0o600,
                f"mode {oct(stat.S_IMODE((tmp / '.env').stat().st_mode))}")
        r.check("the original is backed up",
                any(x.name.startswith(".env.bak.") for x in tmp.iterdir()),
                f"files: {[x.name for x in tmp.iterdir()]}")

    print("\n   a FAILED verification is caught and reported")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out, argv = _run_deploy(tmp, 1, ENV_BEFORE)
        # This is the check that a source grep could not make. `rc=$?` on
        # its own line is unreachable under `set -e`: the shell exits at the
        # failing command, so the operator gets a bare traceback and no
        # guidance about the backup.
        r.check("a failing verification exits non-zero", rc != 0, f"rc={rc}")
        r.check("...and says so, rather than dying silently",
                "verification failed" in out,
                f"the failure path must reach its own message. out={out!r}")
        r.check("...and points at the backup", ".env backup" in out,
                f"out={out!r}")
        r.check("...and leaves no temp verification file",
                not list(tmp.glob(".verify_ibenty*")),
                f"left behind: {[x.name for x in tmp.glob('.verify_ibenty*')]}")

    print("\n   what the verification itself asserts")
    r.check("it posts the real credentials to /auth/login",
            'json={"email": email, "password": password}, timeout=30' in src)
    r.check("...and proves the endpoint authenticates, with a wrong password",
            '"-wrong"' in src,
            "a 200 from an endpoint that accepts anything proves nothing")
    r.check("it refuses to finish if an old key survived",
            '[[ "$left" -eq 0 ]]' in src)



def main() -> int:
    r = Results()
    print(f"target: the xbinder -> ibenty rename, in {REPO}")
    check_module(r)
    check_credentials(r)
    check_profile_key(r)
    check_user_facing_text(r)
    check_deploy_script(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
