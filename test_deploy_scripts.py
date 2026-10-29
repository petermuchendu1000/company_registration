#!/usr/bin/env python3
"""The contract the deploy artifacts must keep.

    python3 test_deploy_scripts.py

Hermetic: each script is RUN in a temporary directory with a stub
interpreter, so nothing touches a real .env and nothing reaches the
network. APP_DIR and PY are overridable in the scripts for exactly this.

Checking these by grepping their source is how a broken one shipped. The
source contained "rc=$?" and a check asserting that string passed, while
under `set -euo pipefail` the shell terminated at the failing command and
that line never ran - the operator got a bare traceback and no guidance.
A check on a string is not a check on a property.

The second bug the same day: the verification was piped in on stdin, and
load_dotenv() with no argument calls find_dotenv(), which walks back
through the CALLER's stack frames. A script read from stdin has no caller
frame, so it died on `assert frame.f_back is not None`. The stub
interpreter here records how it was invoked, so a return to stdin is
caught.

One file rather than one per script, because the contract is the same and
two copies of it would be free to drift - which is the defect this project
has already had twice, in the scorer and in the output path.
"""
from __future__ import annotations

import os
import stat
import sys
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).parent
DEPLOY = REPO / "deploy"

# script -> (args it takes, the .env keys it must end up writing)
SCRIPTS = {
    "set_ibenty_env.sh": (["a@b.c", "pw"],
                          ["IBENTY_EMAIL=a@b.c", "IBENTY_PASSWORD=pw"]),
    "set_ch_key.sh": (["k3y-value"],
                      ["COMPANIES_HOUSE_API_KEY=k3y-value"]),
}

ENV_BEFORE = ("DATABASE_URL=postgresql://app:s3cret@db/company\n"
              "FLASK_SECRET_KEY=keep-me\n")


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


def run_script(script: str, tmp: Path, py_exit: int, args: list[str],
               env_body: str = ENV_BEFORE):
    """Run one deploy script with a stub interpreter that records its argv."""
    (tmp / ".env").write_text(env_body, encoding="utf-8")
    argv_log = tmp / "argv.txt"
    stub = tmp / "stubpy"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s" "$*" > {argv_log}\nexit {py_exit}\n',
        encoding="utf-8")
    stub.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(DEPLOY / script), *args],
        env={**os.environ, "APP_DIR": str(tmp), "PY": str(stub)},
        capture_output=True, text=True, timeout=120)
    argv = argv_log.read_text(encoding="utf-8").strip() if argv_log.exists() else ""
    return proc.returncode, proc.stdout + proc.stderr, argv


def check_script(r: Results, script: str) -> None:
    args, expected_keys = SCRIPTS[script]
    p = DEPLOY / script
    print(f"\n-- {script} --")
    if not r.check("exists", p.is_file()):
        return
    r.check("is executable", bool(p.stat().st_mode & stat.S_IXUSR),
            f"mode {oct(stat.S_IMODE(p.stat().st_mode))}")
    src = p.read_text(encoding="utf-8")
    head = src.split("say()")[0]
    r.check("no credential baked into it",
            "@" not in head.replace("#!/usr/bin/env bash", ""),
            "a credential in the repository is published when it is pushed")
    r.check("it refuses to run outside the app directory",
            'does not exist - is this the server?' in src)

    # ── the success path ────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out, argv = run_script(script, tmp, 0, args)
        r.check("the interpreter gets a real file, not stdin",
                argv.endswith(".py") and argv != "-",
                f"invoked as {argv!r}; '-' is stdin, which makes "
                f"load_dotenv() raise AssertionError")
        r.check("...inside APP_DIR, beside .env", argv.startswith(str(tmp)),
                f"invoked with {argv!r}")
        r.check("a successful verification exits 0", rc == 0, f"rc={rc}\n{out}")
        r.check("...leaving no temp verification file",
                not list(tmp.glob(".verify_*")),
                f"left: {[x.name for x in tmp.glob('.verify_*')]}")

        body = (tmp / ".env").read_text(encoding="utf-8")
        for kv in expected_keys:
            r.check(f"writes {kv.split('=')[0]}", kv in body, f"got {body!r}")
            r.check(f"...exactly once",
                    body.count("\n" + kv.split("=")[0] + "=")
                    + body.startswith(kv.split("=")[0] + "=") == 1,
                    f"python-dotenv keeps the LAST occurrence. got {body!r}")
        r.check("every other key survives",
                "DATABASE_URL=postgresql://app:s3cret@db/company" in body
                and "FLASK_SECRET_KEY=keep-me" in body, f"got {body!r}")
        r.check(".env is left at mode 600",
                stat.S_IMODE((tmp / ".env").stat().st_mode) == 0o600,
                f"mode {oct(stat.S_IMODE((tmp / '.env').stat().st_mode))}")
        r.check("the original is backed up",
                any(x.name.startswith(".env.bak.") for x in tmp.iterdir()),
                f"files: {[x.name for x in tmp.iterdir()]}")

    # ── the failure path ────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rc, out, argv = run_script(script, tmp, 1, args)
        r.check("a failing verification exits non-zero", rc != 0, f"rc={rc}")
        r.check("...and reaches its own message, not a bare traceback",
                "verification failed" in out,
                f"`rc=$?` alone is unreachable under set -e. out={out!r}")
        r.check("...and points at the backup", ".env backup" in out,
                f"out={out!r}")
        r.check("...leaving no temp verification file",
                not list(tmp.glob(".verify_*")),
                f"left: {[x.name for x in tmp.glob('.verify_*')]}")

    # ── re-running must not duplicate a key ─────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        run_script(script, tmp, 0, args)
        first = (tmp / ".env").read_text(encoding="utf-8")
        # Second run against the .env the first one produced.
        rc, out, argv = run_script(script, tmp, 0, args, env_body=first)
        body = (tmp / ".env").read_text(encoding="utf-8")
        for kv in expected_keys:
            name = kv.split("=")[0]
            n = sum(1 for line in body.splitlines()
                    if line.startswith(name + "="))
            r.check(f"re-running leaves exactly one {name}", n == 1,
                    f"{n} lines - a duplicate silently decides which value "
                    f"wins. got {body!r}")
        r.check("re-running still exits 0", rc == 0, f"rc={rc}\n{out}")


def check_prompt_safety(r: Results, script: str) -> None:
    """The prompt must never consume the next line of a pasted block.

    This happened. set_ch_key.sh was handed to an operator in the middle of
    a block of commands; a plain `read` takes the following pasted line as
    its input, so "install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile"
    became the credential and never ran. The script must refuse instead.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / ".env").write_text("DATABASE_URL=keep-me\n", encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(DEPLOY / script)],            # no args: the prompt path
            env={**os.environ, "APP_DIR": str(tmp), "PY": sys.executable},
            input="install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile\n",
            capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
        r.check(f"{script}: a non-terminal prompt refuses", proc.returncode == 2,
                f"rc={proc.returncode} - it must not read a pasted command as "
                f"a credential. out={out[-200:]!r}")
        r.check(f"{script}: ...saying it needs a terminal",
                "needs a terminal" in out, f"out={out[-200:]!r}")
        body = (tmp / ".env").read_text(encoding="utf-8")
        r.check(f"{script}: ...and writes nothing",
                "install -m 644" not in body and body == "DATABASE_URL=keep-me\n",
                f".env became {body!r}")
        # The guard must test that /dev/tty can be OPENED. `[[ -r /dev/tty ]]`
        # is TRUE even where opening fails - measured in a container with no
        # controlling terminal - so it is not a check at all.
        src = (DEPLOY / script).read_text(encoding="utf-8")
        r.check(f"{script}: the guard tests openability, not just existence",
                "(exec </dev/tty)" in src,
                "[[ -r /dev/tty ]] passes where the device cannot be opened")
        r.check(f"{script}: every prompt reads from the terminal",
                all("< /dev/tty" in ln
                    for ln in src.splitlines()
                    if ln.strip().startswith("read -r")),
                "a prompt left on stdin can still eat a pasted line")


def check_caddyfile(r: Results) -> None:
    print("\n-- deploy/Caddyfile security headers --")
    p = DEPLOY / "Caddyfile"
    if not r.check("the Caddyfile exists", p.is_file()):
        return
    src = p.read_text(encoding="utf-8")

    # DENY refuses framing by EVERYONE, including the site itself, and the
    # console previews a certificate PDF in a same-origin iframe
    # (static/app.html:2288 builds the root-relative /api/v2/dl?...&inline=1,
    # framed at :2293). Reproduced in a real browser: with DENY, Chromium
    # refused - "Refused to display ... because it set 'X-Frame-Options' to
    # 'deny'" - and with SAMEORIGIN it rendered.
    r.check("X-Frame-Options is SAMEORIGIN, not DENY",
            'X-Frame-Options "SAMEORIGIN"' in src,
            "DENY blocks the certificate preview, which frames this same "
            "origin")
    r.check("...and DENY is not still set anywhere",
            'X-Frame-Options "DENY"' not in src, "two values would conflict")
    r.check("...with the reason recorded, so it is not reverted blindly",
            "same-origin" in src.lower() and "preview" in src.lower(),
            "a future edit will put DENY back unless the file says why")

    # The protection the header exists for must survive: cross-origin
    # framing stays refused. A CSP frame-ancestors would override
    # X-Frame-Options, so a permissive one would silently undo this.
    r.check("no frame-ancestors directive overrides it",
            "frame-ancestors" not in src.lower(),
            "CSP frame-ancestors takes precedence over X-Frame-Options")
    for header in ("X-Content-Type-Options", "Strict-Transport-Security",
                   "Referrer-Policy"):
        r.check(f"{header} is still set", header in src,
                "the other security headers must not be lost in the edit")


def main() -> int:
    r = Results()
    print(f"target: the .env-rewriting scripts in {DEPLOY}")

    # A script added later that rewrites .env but is not listed above would
    # go unchecked, and the contract exists precisely because getting it
    # wrong is silent.
    writers = sorted(p.name for p in DEPLOY.glob("*.sh")
                     if 'ENV_FILE="$APP_DIR/.env"' in p.read_text(encoding="utf-8"))
    r.check("every .env-rewriting script in deploy/ is covered here",
            writers == sorted(SCRIPTS),
            f"found {writers}, covered {sorted(SCRIPTS)}")

    for script in sorted(SCRIPTS):
        check_script(r, script)
        check_prompt_safety(r, script)
    check_caddyfile(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
