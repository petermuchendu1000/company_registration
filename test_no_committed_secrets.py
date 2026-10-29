#!/usr/bin/env python3
"""No tracked file may carry a credential.

    python3 test_no_committed_secrets.py

apps/template-shift-journal/app/build.gradle.kts is git-tracked, and
apps/generator/flavor_writer.py:141-172 rewrites a block inside it on every
build, emitting each flavor's keystore password as a plaintext literal. One
such password sat in that file from commit 8d0f76f (2026-07-06) until it was
removed, in a repository whose visibility is public.

Nothing crashed, which is why it lasted: the value is only read by Gradle at
sign time, so a leaked password looks exactly like a working build.

The same thing then happened a second time, with a different kind of
credential, and this file did not catch it: the LIVE Namecheap API key and
the whitelisted client IP were pasted into test_check_stage.py's ENV_BODY
fixture while a defect was being reproduced, and committed - because this
file only ever looked for storePassword and keyPassword, so an API key in
a .env-shaped fixture was outside everything it scanned. A guard that
covers one credential and calls itself "no committed secrets" is worse
than no guard: it is read as having checked.

This file guards three things:

  1. No tracked file assigns storePassword/keyPassword to a literal. The
     generator's own f-string at flavor_writer.py:170 is NOT a finding - it
     is the code that produces the value, and it contains a {placeholder},
     which is how the two are told apart.
  2. No tracked file carries a plausible API key or token for any of the
     services this project uses. A fixture needs a value of the right
     SHAPE, never a working one, so the scan allows the obviously-synthetic
     forms (all one repeated character, or a value naming itself a sample)
     and reports everything else for a human to look at.
  3. Both GENERATED FLAVORS markers survive, in order. Emptying that block
     is what removed the first secret, and flavor_writer.py:151-157 raises
     if either marker is missing - so a careless cleanup that deleted them
     would break every future build.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent
APP_DIR = REPO / "apps" / "template-shift-journal" / "app"
# The TRACKED source. build.gradle.kts beside it is generated and ignored;
# it is the file that carries the keystore passwords.
BUILD_GRADLE = APP_DIR / "build.gradle.kts.template"
GENERATED_GRADLE = APP_DIR / "build.gradle.kts"

# A literal assignment: no { } inside, so the generator's f-string template
# does not match. Verified against four cases in the self-test below.
SECRET = re.compile(r'(storePassword|keyPassword)\s*=\s*"([^"{}]+)"')

# A credential assigned to one of this project's known secret names. The
# name list is explicit rather than a generic "anything that looks like a
# key", because a generic hex scan fires on every git SHA, colour and hash
# in the tree and a guard that cries wolf gets switched off.
#
# The value may be quoted or bare (a .env line is bare), and must be long
# enough to be a credential rather than a flag - 16 characters, below the
# 32 of a Namecheap key and the 36 of a Companies House one, so a shorter
# format still trips it. A {placeholder} or a $VAR is an indirection, not
# a secret.
CRED_NAMES = (
    "NAMECHEAP_API_KEY", "NAMECHEAP_PASSWORD", "COMPANIES_HOUSE_API_KEY",
    "IBENTY_PASSWORD", "XBINDER_PASSWORD", "FLASK_SECRET_KEY",
    "SUPERADMIN_PASSWORD", "DNB_API_KEY", "SUPABASE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
)
# [ \t]* around the separator, not \s*: with \s* an EMPTY value lets the
# match run past the end of the line and read the NEXT key's name as the
# value, which is how this reported DEPLOYMENT.md's blank .env template as
# carrying two credentials.
CRED = re.compile(
    r'\b(' + "|".join(CRED_NAMES) +
    r')[ \t]*[=:][ \t]*"?([^"\'\s{}$,)\]]{16,})"?')

# A fixture value that is obviously not real: one character repeated, a
# value that says what it is, or one of the conventional dummy hex words.
# These are what a test SHOULD contain.
SYNTHETIC = re.compile(
    r'^(?:(.)\1{15,}|.*(?:example|sample|dummy|fake|placeholder|changeme|'
    r'test[-_]?key|xxx+|your[-_]|deadbeef|cafebabe|0123456789abcdef).*)$',
    re.I)

# A fixture inside a Python string literal ends with an escaped newline -
# the two characters backslash and n - which is part of the source, not of
# the value. Left on, it defeats the repeated-character test: a line of
# thirty-two zeroes reads as thirty-two zeroes followed by something else.
_TRAILING_ESCAPE = re.compile(r'(?:\\[nrt])+$')

MARKER_START = "// >>> GENERATED FLAVORS START"
MARKER_END = "// <<< GENERATED FLAVORS END"


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


def self_test(r: Results) -> None:
    """A scanner that cannot fire would pass every check below."""
    print("\n-- the scanner discriminates --")
    # The field name is assembled at runtime and the value is synthetic.
    # Written out literally, these fixtures match the scanner and it reports
    # itself - which is exactly what happened the moment this file became
    # tracked. The password that was really committed is deliberately not
    # reproduced here; any 32-hex literal exercises the pattern equally.
    sp = "store" + "Password"
    fake = "0123456789abcdef0123456789abcdef"
    cases = [
        ("fires on a literal assignment", f'    {sp} = "{fake}"', True),
        ("ignores the generator f-string that produces one",
         "f'    " + sp + " = \"{_gradle_escape(ks.store_password)}\"',", False),
        ("ignores an env-var indirection",
         f'    {sp} = System.getenv("KS_PASS")', False),
        ("ignores an empty literal", f'    {sp} = ""', False),
    ]
    for label, text, want in cases:
        r.check(label, bool(SECRET.search(text)) is want)

    # The credential scanner. The real key that was committed is not
    # reproduced here - any 32-hex value of the same shape exercises the
    # pattern identically, which is the whole point being made.
    nc = "NAMECHEAP" + "_API_KEY"
    cred_cases = [
        ("fires on a bare .env assignment",
         f"{nc}=1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d", True),
        ("fires on a quoted one", f'{nc} = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"',
         True),
        ("fires on a credential on a line of other text",
         f'    "{nc}=1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d\\n"', True),
        ("fires on a non-hex token too",
         "IBENTY" + "_PASSWORD=sUperSecretPassphrase1", True),
        ("ignores an env-var indirection", f'{nc}=$NAMECHEAP_KEY', False),
        ("ignores a format placeholder", f'{nc}={{key}}', False),
        ("ignores an empty value", f"{nc}=", False),
        ("ignores a value too short to be a credential", f"{nc}=abc123", False),
        ("ignores a name that is not a credential",
         "COMPANY_NUMBER=00000000000000000001", False),
        # The blank .env template in DEPLOYMENT.md. With \\s* around the
        # separator this matched, taking the NEXT key's name as the value.
        ("does not read the next line's key as a value",
         f"COMPANIES_HOUSE_API_KEY=\n{nc}_USER=\n", False),
        ("...even with several blank lines between",
         f"COMPANIES_HOUSE_API_KEY=\n\n\n{nc}_USER=\n", False),
    ]
    for label, text, want in cred_cases:
        r.check(label, bool(CRED.search(text)) is want, f"on {text!r}")

    synth_cases = [
        ("a repeated-character fixture is synthetic",
         "00000000000000000000000000000000", True),
        ("...and so is one that names itself", "sample-key-value-here", True),
        ("...and 'your-key-here'", "your-api-key-here", True),
        ("a real-looking key is NOT synthetic",
         "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d", False),
    ]
    for label, value, want in synth_cases:
        r.check(label, bool(SYNTHETIC.match(value)) is want, f"on {value!r}")

    zeros = "0" * 32
    r.check("a Python-source fixture's escaped newline is stripped first",
            bool(SYNTHETIC.match(_TRAILING_ESCAPE.sub("", zeros + "\\n"))),
            "without stripping it, 32 zeroes plus '\\n' is not a repeated "
            "character and the fixture reports itself")
    r.check("...and stripping does not make a real key look synthetic",
            not SYNTHETIC.match(
                _TRAILING_ESCAPE.sub("", "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d\\n")))


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                         capture_output=True, text=True, check=True)
    return [REPO / n for n in out.stdout.split("\0") if n]


def scan_tracked(r: Results) -> None:
    print("\n-- no tracked file carries a literal signing password --")
    files = tracked_files()
    # An empty file list would make the check below vacuously pass.
    if not r.check("git ls-files returned a non-empty list", bool(files),
                   "cannot scan: no tracked files reported"):
        return
    print(f"  ({len(files)} tracked files)")

    hits: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue                      # binary or absent; nothing to read
        for m in SECRET.finditer(text):
            line = text[:m.start()].count("\n") + 1
            hits.append(f"{f.relative_to(REPO)}:{line} {m.group(1)}")
    r.check("no literal storePassword/keyPassword in tracked files",
            not hits, "; ".join(hits))

    # The scan that was missing. This file itself is scanned like any
    # other, which is why its own fixtures are assembled at runtime.
    cred_hits: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for m in CRED.finditer(text):
            name = m.group(1)
            value = _TRAILING_ESCAPE.sub("", m.group(2))
            if SYNTHETIC.match(value):
                continue
            line = text[:m.start()].count("\n") + 1
            # The value is NOT printed: this output is read in terminals
            # and pasted into messages, which is one more place for it to
            # travel. The location and the name are enough to act on.
            cred_hits.append(f"{f.relative_to(REPO)}:{line} {name} "
                             f"(<{len(value)} chars>)")
    r.check("no tracked file carries a credential for a known service",
            not cred_hits,
            "a credential in a public repository is published the moment it "
            "is pushed, and rotating it is the only fix. Found: "
            + "; ".join(cred_hits))


def check_credential_files_ignored(r: Results) -> None:
    """The files the deploy scripts leave beside .env must be ignored.

    set_ch_key.sh:82 and set_ibenty_env.sh:85 copy .env to
    .env.bak.<timestamp> in the repository root, and stage the replacement
    as .env.new.XXXXXX. Each is a verbatim copy of every production
    credential. Untracked is not safe: `git add -A` stages them, and this
    repository is public.

    Asked of git itself rather than by reading .gitignore, because what
    matters is the rule's EFFECT - a pattern can be present and still not
    match, and a later rule can negate an earlier one.
    """
    print("\n-- the deploy scripts' credential files are ignored --")
    # Names in the shapes the scripts actually produce.
    for name in (".env.bak.20260101-000000", ".env.new.aB3xYz", ".env"):
        p = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO)
        r.check(f"git ignores {name}", p.returncode == 0,
                f"check-ignore exited {p.returncode} - this file would be "
                f"staged by `git add -A` and published on push")

    # The check must be able to fail, or it says nothing about the rules.
    # --no-index: check-ignore consults the index by default, so a TRACKED
    # path such as api_v2.py answers "not ignored" whatever the rules say,
    # and this control could not fail.
    p = subprocess.run(["git", "check-ignore", "-q", "--no-index", "api_v2.py"],
                       cwd=REPO)
    r.check("...and does NOT ignore a source file",
            p.returncode != 0,
            "check-ignore claims to ignore api_v2.py, so the four results "
            "above prove nothing")

    # The generated build file. flavor_writer.write_flavors_gradle splices
    # every flavor's storePassword and keyPassword into it as plaintext
    # literals, so it must never be tracked - that is how one such password
    # reached commit 8d0f76f. Tracking it also made every build dirty the
    # tree, so the next `git pull` conflicted against a file nobody edited.
    p = subprocess.run(["git", "check-ignore", "-q", str(GENERATED_GRADLE)],
                       cwd=REPO)
    r.check("git ignores the generated build.gradle.kts", p.returncode == 0,
            "it carries the keystore passwords and a build rewrites it")
    p = subprocess.run(["git", "ls-files", "--error-unmatch",
                        str(GENERATED_GRADLE.relative_to(REPO))],
                       cwd=REPO, capture_output=True, text=True)
    r.check("...and it is not tracked", p.returncode != 0,
            "it is in the index despite the ignore rule - `git rm --cached` it")
    r.check("the template IS tracked, so a checkout can still build",
            subprocess.run(["git", "ls-files", "--error-unmatch",
                            str(BUILD_GRADLE.relative_to(REPO))],
                           cwd=REPO, capture_output=True).returncode == 0,
            f"{BUILD_GRADLE.name} is the only source for the generated file")
    r.check("...and the template carries no keystore password",
            not SECRET.search(BUILD_GRADLE.read_text(encoding="utf-8")),
            "the tracked template must hold no generated content")

    # The scripts must keep producing the names that are ignored. If one
    # changes its backup name, the rule above silently stops covering it.
    for script, needle in (("set_ch_key.sh", '"${ENV_FILE}.new.XXXXXX"'),
                           ("set_ibenty_env.sh", '"${ENV_FILE}.new.XXXXXX"')):
        src = (REPO / "deploy" / script).read_text(encoding="utf-8")
        r.check(f"{script} still names its temp file .env.new.*",
                needle in src, f"the ignore rule no longer covers it")
        r.check(f"{script} still names its backup .env.bak.*",
                '"$ENV_FILE.bak.$(date' in src,
                "the ignore rule no longer covers it")


def check_markers(r: Results) -> None:
    print("\n-- the generator can still write its block --")
    if not r.check("the tracked template exists", BUILD_GRADLE.exists(),
                   str(BUILD_GRADLE)):
        return
    src = BUILD_GRADLE.read_text(encoding="utf-8")
    s, e = src.find(MARKER_START), src.find(MARKER_END)
    r.check("the START marker is present", s >= 0, MARKER_START)
    r.check("the END marker is present", e >= 0, MARKER_END)
    r.check("START precedes END", s >= 0 and e > s,
            f"start={s} end={e}; flavor_writer.py:153 rejects end < start")
    between = src[s + len(MARKER_START):e] if (s >= 0 and e > s) else ""
    r.check("the committed block declares no signingConfig",
            "signingConfig" not in between,
            "the committed template carries a generated signing block; it "
            "should be empty and regenerated at build time")


def main() -> int:
    r = Results()
    print(f"repo: {REPO}")
    self_test(r)
    scan_tracked(r)
    check_credential_files_ignored(r)
    check_markers(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
