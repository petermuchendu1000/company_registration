#!/usr/bin/env python3
"""No tracked file may carry a literal signing password.

    python3 test_no_committed_secrets.py

apps/template-shift-journal/app/build.gradle.kts is git-tracked, and
apps/generator/flavor_writer.py:141-172 rewrites a block inside it on every
build, emitting each flavor's keystore password as a plaintext literal. One
such password sat in that file from commit 8d0f76f (2026-07-06) until it was
removed, in a repository whose visibility is public.

Nothing crashed, which is why it lasted: the value is only read by Gradle at
sign time, so a leaked password looks exactly like a working build.

This file guards two things:

  1. No tracked file assigns storePassword/keyPassword to a literal. The
     generator's own f-string at flavor_writer.py:170 is NOT a finding - it
     is the code that produces the value, and it contains a {placeholder},
     which is how the two are told apart.
  2. Both GENERATED FLAVORS markers survive, in order. Emptying that block
     is what removed the secret, and flavor_writer.py:151-157 raises if
     either marker is missing - so a careless cleanup that deleted them
     would break every future build.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent
BUILD_GRADLE = (REPO / "apps" / "template-shift-journal" / "app"
                / "build.gradle.kts")

# A literal assignment: no { } inside, so the generator's f-string template
# does not match. Verified against four cases in the self-test below.
SECRET = re.compile(r'(storePassword|keyPassword)\s*=\s*"([^"{}]+)"')

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


def check_markers(r: Results) -> None:
    print("\n-- the generator can still write its block --")
    if not r.check("build.gradle.kts exists", BUILD_GRADLE.exists(),
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
    check_markers(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
