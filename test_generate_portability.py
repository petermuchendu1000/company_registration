#!/usr/bin/env python3
"""The app generator must parse and resolve on the Python production runs.

    python3 test_generate_portability.py

Two defects prompted this file, both in apps/generator/generate.py:

  1. Three f-strings nested double quotes inside a double-quoted f-string.
     That is PEP 701, which arrived in Python 3.12. Debian 12 ships Python
     3.11 and DEPLOYMENT.md pins Debian 12, so on the server the module
     raised SyntaxError at import. develop_app.py imports it at module
     scope, so that script could not start at all - not even for its
     Companies House, DUNS, domain and email stages, which never touch the
     app builder.

  2. The branch reporting a completely successful build referenced
     OUTPUT_DIR, a name never defined in the module. The partial-success
     branch beside it had no such reference, so only the happy path raised
     NameError, and backup_and_make_assets below it never ran.

Both are invisible to a 3.12+ interpreter reading the file, which is why
the checks here do not rely on the running interpreter's own parser:
ast.parse(..., feature_version=(3, 11)) was measured against the broken
source and PARSED it, so that call cannot detect this defect class and is
deliberately not used.
"""
from __future__ import annotations

import ast
import builtins
import dis
import re
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).with_name("apps") / "generator"
TARGET = PKG / "generate.py"

# Below this, nesting a quote inside a same-quoted f-string is a SyntaxError.
PEP701_VERSION = (3, 12)


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


# ── 1. compile under a real pre-3.12 interpreter ────────────────────────
def older_interpreters() -> list[tuple[str, tuple[int, int]]]:
    """Every python3.x on PATH older than PEP 701, newest first."""
    found = []
    for minor in range(6, PEP701_VERSION[1]):
        exe = shutil.which(f"python3.{minor}")
        if exe:
            found.append((exe, (3, minor)))
    return sorted(found, key=lambda t: t[1], reverse=True)


def compiles_under(exe: str, path: Path) -> tuple[bool, str]:
    """True if `exe` can compile `path`. Uses py_compile for a real parse."""
    code = (
        "import py_compile, sys, tempfile\n"
        "try:\n"
        "    py_compile.compile(sys.argv[1],"
        " cfile=tempfile.mktemp(suffix='.pyc'), doraise=True)\n"
        "    print('OK')\n"
        "except py_compile.PyCompileError as e:\n"
        "    print('ERR', e)\n"
    )
    p = subprocess.run([exe, "-c", code, str(path)],
                       capture_output=True, text=True, timeout=120)
    out = (p.stdout + p.stderr).strip()
    return out.startswith("OK"), out


def check_old_interpreters(r: Results) -> None:
    print("\n-- parses on the Python the server runs --")
    olds = older_interpreters()
    if not olds:
        # Not a pass. Saying nothing here is how the defect survived.
        r.check(f"an interpreter older than {PEP701_VERSION} is available "
                "to test against", False,
                "no python3.6-3.11 on PATH, so the check that actually "
                "catches PEP 701 syntax could not run. Install one, or run "
                "this suite on a host that has it.")
        return
    for exe, ver in olds:
        ok, out = compiles_under(exe, TARGET)
        r.check(f"generate.py compiles on python{ver[0]}.{ver[1]}", ok,
                out[:400])


# ── 2. lexical scan for the exact defect class ──────────────────────────
_FSTR = re.compile(r'(?<![A-Za-z0-9_"\'])([fF][rRbB]?|[rRbB][fF])(["\'])')


def nested_same_quote(line: str) -> bool:
    """Does an f-string on this line contain its own delimiter inside {...}?

    Scans rather than regexes the whole construct, because the delimiter is
    legal at depth 0 (it ends the string) and illegal at depth >0 before
    3.12, and only a brace-depth count tells those apart.
    """
    i = 0
    while True:
        m = _FSTR.search(line, i)
        if not m:
            return False
        q = m.group(2)
        start = m.end()
        if line.startswith(q * 2, start - 1):      # triple-quoted: other rules
            i = start + 2
            continue
        depth = 0
        j = start
        while j < len(line):
            ch = line[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "{":
                if line.startswith("{{", j):
                    j += 2
                    continue
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            elif ch == q:
                if depth > 0:
                    return True
                break                               # f-string closed
            j += 1
        i = max(j + 1, start)


def check_lexical(r: Results) -> None:
    print("\n-- no f-string nests its own quote character --")
    # Self-test first: a checker that cannot fire proves nothing.
    bad = 'x = f"AAB built: {Path(d["aab_path"]).name}"'
    good = "x = f\"AAB built: {Path(d['aab_path']).name}\""
    r.check("the scanner fires on the known-bad form",
            nested_same_quote(bad), "scanner is blind; every result below "
            "would be a false pass")
    r.check("the scanner stays quiet on the fixed form",
            not nested_same_quote(good), "scanner false-positives")

    for py in sorted(PKG.glob("*.py")):
        hits = [n for n, ln in enumerate(py.read_text(encoding="utf-8")
                                         .splitlines(), 1)
                if nested_same_quote(ln)]
        r.check(f"{py.name} nests no same-type quote in an f-string",
                not hits, f"lines {hits} would be a SyntaxError before "
                f"{PEP701_VERSION}")


# ── 3. every global the module loads actually resolves ──────────────────
def check_globals_resolve(r: Results) -> None:
    print("\n-- every global name the module references exists --")
    sys.path.insert(0, str(Path(__file__).parent))
    import apps.generator.generate as mod

    src = TARGET.read_text(encoding="utf-8")
    code = compile(src, str(TARGET), "exec")

    # Bytecode, not the AST: LOAD_GLOBAL names only real global lookups, so
    # function parameters and locals cannot show up as false positives the
    # way they do in a bare ast.Name walk.
    missing: set[str] = set()

    def walk(c) -> None:
        for ins in dis.get_instructions(c):
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                name = ins.argval
                if (isinstance(name, str)
                        and not hasattr(mod, name)
                        and not hasattr(builtins, name)):
                    missing.add(name)
        for const in c.co_consts:
            if hasattr(const, "co_code"):
                walk(const)

    walk(code)
    r.check("no unresolved global in generate.py", not missing,
            f"these would raise NameError when their branch runs: "
            f"{sorted(missing)}")

    # The specific name that was missing, asserted directly so a future
    # rename cannot quietly reintroduce it. Comment lines are skipped: the
    # comment at the fixed line names OUTPUT_DIR in order to explain why it
    # must not come back, and failing on that would be failing on the
    # documentation of the fix.
    live = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    r.check("OUTPUT_DIR is not referenced in live code "
            "(it is defined nowhere here)",
            "OUTPUT_DIR" not in live,
            "OUTPUT_DIR is back in the source; run_pipeline.py defines its "
            "own, this module never has")
    r.check("the success branch names a directory that exists as a constant",
            hasattr(mod, "COMPANIES_DIR"),
            "COMPANIES_DIR missing from the module")


# ── 4. the rewrite did not change the output ────────────────────────────
def check_equivalence(r: Results) -> None:
    print("\n-- the rewritten f-strings produce identical text --")
    if sys.version_info < PEP701_VERSION:
        print(f"  SKIP  needs >= {PEP701_VERSION} to evaluate the old form "
              f"at all (running {sys.version_info[:2]})")
        return

    q = '"'
    # Built from a quote variable so this file itself stays pre-3.12 clean.
    old_aab = (f"f{q}AAB built: {{Path(d[{q}aab_path{q}]).name "
               f"if d[{q}aab_path{q}] else {q}missing{q}}}{q}")
    new_aab = ("f\"AAB built: {Path(d['aab_path']).name "
               "if d['aab_path'] else 'missing'}\"")

    cases = [
        ("a real artifact path", {"aab_path": "/out/app-c02591663-release.aab"}),
        ("an empty path (the missing boundary)", {"aab_path": ""}),
    ]
    for label, d in cases:
        ns = {"Path": Path, "d": d}
        got_old = eval(old_aab, ns)       # noqa: S307 - fixed literals above
        got_new = eval(new_aab, ns)       # noqa: S307
        r.check(f"old and new agree on {label}", got_old == got_new,
                f"old={got_old!r} new={got_new!r}")

    # Everything above compares two literals written in THIS file, so it
    # proves the rewrite was faithful but says nothing about the source that
    # actually ships. A mutation that changed generate.py's rendered text
    # survived this suite until the checks below were added: they pull the
    # f-strings out of generate.py itself and pin what each one renders.
    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "generate_from_pipeline_results"), None)
    if not r.check("generate_from_pipeline_results is present", fn is not None):
        return

    shipped = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.JoinedStr):
            seg = ast.get_source_segment(src, node)
            if seg and " built: " in seg:
                shipped[seg.split(" built: ")[0].split('"')[-1]] = seg

    class _C:
        company_number = "CN"

    def render(seg: str, apk: str, aab: str) -> str:
        ns = {"Path": Path, "c": _C(),
              "output": {"CN": {"apk_path": apk, "aab_path": aab}}}
        return eval(seg, ns)            # noqa: S307 - segment from our source

    APK, AAB = "/out/app-cCN-release.apk", "/out/app-cCN-release.aab"
    expected = [
        ("AAB", APK, AAB, "AAB built: app-cCN-release.aab"),
        ("AAB", "", "", "AAB built: missing"),
        ("APK", APK, AAB, "APK built: app-cCN-release.apk"),
        ("APK", "", "", "APK built: missing"),
        ("APK/AAB", APK, AAB,
         "APK/AAB built: app-cCN-release.apk, app-cCN-release.aab"),
        ("APK/AAB", "", "", "APK/AAB built: missing, missing"),
    ]
    r.check("all three shipped message f-strings were found",
            set(shipped) == {"AAB", "APK", "APK/AAB"},
            f"found {sorted(shipped)}")
    for key, apk, aab, want in expected:
        seg = shipped.get(key)
        if seg is None:
            r.check(f"shipped {key!r} message exists", False)
            continue
        got = render(seg, apk, aab)
        r.check(f"shipped {key!r} renders {want!r}", got == want,
                f"got {got!r}")

    # And the real source must use the single-quoted form, not just an
    # equivalent one in this test.
    r.check("generate.py uses the single-quoted subscripts",
            "['aab_path']" in src and "['apk_path']" in src,
            "the fix is not in the source being shipped")


def main() -> int:
    r = Results()
    print(f"target: {TARGET}")
    check_old_interpreters(r)
    check_lexical(r)
    check_globals_resolve(r)
    check_equivalence(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
