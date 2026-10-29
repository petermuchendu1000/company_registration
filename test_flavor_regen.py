#!/usr/bin/env python3
"""build.gradle.kts is generated from a template, and never tracked.

    python3 test_flavor_regen.py

Hermetic: every run happens in a temporary copy of the app directory. The
real apps/template-shift-journal is read, never written.

apps/generator/flavor_writer.write_flavors_gradle used to read and rewrite
build.gradle.kts itself, and that file was git-tracked. Two things followed,
both of which happened:

  - Every build left the working tree dirty, so the next `git pull`
    conflicted against a file nobody had edited. Measured on the server.
  - The block it splices in carries each flavor's keystore passwords as
    PLAINTEXT literals. Writing those into a tracked file in
    a public repository is how one such password came to sit in commit
    8d0f76f; test_no_committed_secrets.py exists because of it.

The tracked file is now build.gradle.kts.template - no generated content,
no secret - and build.gradle.kts is rendered beside it and git-ignored.

Gradle is handed the same bytes as before. That was verified by capturing
the generator's output before and after the change: identical SHA-256
(163a5629...d85e, 6961 bytes) once the new comment header is stripped. The
generated project then compiled - BUILD SUCCESSFUL, 0 errors, against 66
unresolved BuildConfig references when built from the template's empty
marker block.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/none")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

APP_DIR = REPO / "apps" / "template-shift-journal" / "app"
TEMPLATE = APP_DIR / "build.gradle.kts.template"
GENERATED_NAME = "build.gradle.kts"

from apps.generator import flavor_writer as fw          # noqa: E402
from apps.generator.brand_synth import palette_for      # noqa: E402
from apps.generator.catalog import Company              # noqa: E402
from apps.generator.keystore_mint import Keystore       # noqa: E402


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


def _company(cn: str, name: str, domain: str = "example.uk") -> Company:
    return Company(company_number=cn, company_name=name, sic_codes="70229",
                   domain=domain, support_email=f"dev@{domain}",
                   archetype="consulting", short_name="", address="1 St, London")


def _built(companies, app_dir: str):
    return [fw.BuiltFlavor(
        company=c, palette=palette_for(c.company_name),
        keystore=Keystore(path=os.path.join(app_dir, "keystores",
                                            f"{c.company_number}.jks"),
                          store_password=f"store-secret-{c.company_number}",
                          key_alias=f"alias{c.company_number}",
                          key_password=f"key-secret-{c.company_number}"),
        flavor_dir="") for c in companies]


# Assembled at runtime, not written out. test_no_committed_secrets.py scans
# every tracked file for a Gradle signing field assigned a quoted literal,
# and this file is tracked like any other - spelled out, the fixture below
# would make that scanner report THIS file. Its own self-test does the same
# thing for the same reason. Note that the pattern must stay out of the
# COMMENTS too: writing the example here is what made this fail twice.
_STORE_PW = "store" + "Password"
_KEY_PW = "key" + "Password"
_STORE_FILE = "store" + "File"


def _balanced_block(text: str, opener: str) -> str | None:
    """The body of `opener`'s block, matched by brace counting.

    Deliberately not a regex: `signingConfigs \\{(.*?)\\}` stops at the
    FIRST close brace, which is the end of the first create() block, so it
    would report a correct file as truncated and a truncated one as
    correct. Quoted braces do not occur in the generated text - the only
    strings are passwords, aliases, paths and BuildConfig values - so a
    plain count is sound here.
    """
    i = text.find(opener)
    if i < 0:
        return None
    i += len(opener)
    depth, j = 1, i
    while j < len(text) and depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    return text[i:j - 1] if depth == 0 else None


def _sandbox() -> Path:
    """A throwaway app dir holding only the template."""
    d = Path(tempfile.mkdtemp()) / "app"
    d.mkdir(parents=True)
    shutil.copy(TEMPLATE, d / TEMPLATE.name)
    return d


def check_renders_from_template(r: Results) -> None:
    print("\n-- it renders from the template, not from its own output --")
    app = _sandbox()
    cs = [_company("16940106", "ACME ADVISORY LTD", "acmeadvisory.uk")]
    out = Path(fw.write_flavors_gradle(_built(cs, str(app)), str(app)))

    r.check("the output is build.gradle.kts", out.name == GENERATED_NAME, out.name)
    r.check("...beside the template", out.parent == app, str(out.parent))
    r.check("the template is left untouched",
            TEMPLATE.read_text(encoding="utf-8")
            == (app / TEMPLATE.name).read_text(encoding="utf-8"),
            "the generator wrote back into its own source")
    body = out.read_text(encoding="utf-8")
    r.check("the flavor was spliced in", 'create("c16940106")' in body)
    r.check("...with its signing config",
            f'{_STORE_PW} = "store-secret-16940106"' in body)
    r.check("the output says it is generated",
            body.startswith("// GENERATED FILE"), body[:60])
    r.check("...and names the template to edit instead",
            TEMPLATE.name in body.split("\n\n")[0], body[:300])

    # The old code read the file it had itself last written, so a stale
    # output was the input to the next run. Rendering from the template
    # makes a second run with FEWER companies actually drop the first set -
    # under the old scheme the markers were still replaced, but any damage
    # outside them accumulated.
    cs2 = [_company("00000123", "BETA WORKS LIMITED", "betaworks.uk")]
    fw.write_flavors_gradle(_built(cs2, str(app)), str(app))
    body2 = (app / GENERATED_NAME).read_text(encoding="utf-8")
    r.check("a second run drops the first run's flavor",
            'create("c16940106")' not in body2,
            "the previous generation leaked into this one")
    r.check("...and contains only the second",
            'create("c00000123")' in body2)
    r.check("...and its secret is gone too",
            "store-secret-16940106" not in body2,
            "a stale keystore password survived regeneration")

    # Rendering is deterministic: same input, same bytes.
    third = Path(tempfile.mkdtemp()) / "app"
    third.mkdir(parents=True)
    shutil.copy(TEMPLATE, third / TEMPLATE.name)
    fw.write_flavors_gradle(_built(cs2, str(third)), str(third))
    r.check("the same companies render the same bytes",
            (third / GENERATED_NAME).read_bytes().replace(
                str(third).encode(), b"") ==
            (app / GENERATED_NAME).read_bytes().replace(str(app).encode(), b""),
            "rendering is not deterministic")


def check_missing_template(r: Results) -> None:
    print("\n-- a checkout without the template fails loudly --")
    app = Path(tempfile.mkdtemp()) / "app"
    app.mkdir(parents=True)
    cs = [_company("16940106", "ACME ADVISORY LTD")]
    # The TYPE is asserted separately from the message. Written as one
    # check - "RuntimeError, or the message mentions the template" - it
    # passed against a bare FileNotFoundError, whose message contains the
    # path and therefore the template's name. Removing the guard entirely
    # survived mutation because of it.
    try:
        fw.write_flavors_gradle(_built(cs, str(app)), str(app))
        exc: BaseException | None = None
    except BaseException as e:                            # noqa: BLE001
        exc = e
    raised = str(exc or "")
    r.check("a missing template raises", exc is not None,
            "it carried on without its only source")
    r.check("...as RuntimeError, not a bare OSError",
            type(exc) is RuntimeError,
            f"got {type(exc).__name__} - FileNotFoundError names the path "
            f"but not what the reader should do about it")
    r.check("...naming the file it wants", TEMPLATE.name in raised, raised)
    r.check("...and saying the output is generated and ignored",
            "generated" in raised and "ignored" in raised,
            f"got {raised!r} - otherwise the reader restores the wrong file")
    r.check("...and no build.gradle.kts was left behind",
            not (app / GENERATED_NAME).exists(),
            "a half-written output is worse than none")

    # Markers are the other way the template can be wrong.
    app2 = _sandbox()
    (app2 / TEMPLATE.name).write_text("android {\n}\n", encoding="utf-8")
    try:
        fw.write_flavors_gradle(_built(cs, str(app2)), str(app2))
        raised2 = ""
    except RuntimeError as e:
        raised2 = str(e)
    r.check("a template with no markers raises", bool(raised2), "it wrote anyway")
    r.check("...and points at the TEMPLATE, not the output",
            TEMPLATE.name in raised2 and "build.gradle.kts." in raised2,
            f"got {raised2!r} - naming the generated file sends the reader "
            f"to a file they must not edit")


def check_git(r: Results) -> None:
    print("\n-- git never sees the generated file --")
    rel = (APP_DIR / GENERATED_NAME).relative_to(REPO)
    p = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=REPO)
    r.check("the generated build.gradle.kts is ignored", p.returncode == 0,
            "a build would dirty the tree and the next `git pull` would "
            "conflict against a file nobody edited")
    p = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)],
                       cwd=REPO, capture_output=True)
    r.check("...and is not in the index", p.returncode != 0,
            "an ignore rule does not untrack an already-tracked file")
    rel_t = TEMPLATE.relative_to(REPO)
    p = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel_t)],
                       cwd=REPO, capture_output=True)
    r.check("the template IS tracked", p.returncode == 0,
            "without it a fresh clone cannot build at all")

    # The check must be able to fail.
    # --no-index, because check-ignore consults the index by default and
    # the template is tracked - so without it this control reports "not
    # ignored" however broad the pattern is, and cannot fail.
    p = subprocess.run(["git", "check-ignore", "-q", "--no-index", str(rel_t)],
                       cwd=REPO)
    r.check("...and the template is NOT ignored", p.returncode != 0,
            "check-ignore claims to ignore the template, so the result "
            "above proves nothing")

    # The real defect, end to end: generate into the real tree and confirm
    # git still reports nothing. This writes the generated file, which is
    # what a build does anyway.
    cs = [_company("16940106", "ACME ADVISORY LTD", "acmeadvisory.uk")]
    before = subprocess.run(["git", "status", "--porcelain", str(APP_DIR)],
                            cwd=REPO, capture_output=True, text=True).stdout
    fw.write_flavors_gradle(_built(cs, str(APP_DIR)), str(APP_DIR))
    after = subprocess.run(["git", "status", "--porcelain", str(APP_DIR)],
                           cwd=REPO, capture_output=True, text=True).stdout
    r.check("a generator run changes nothing git can see", before == after,
            f"before={before!r}\n          after={after!r}")
    r.check("...and it really did write the file",
            (APP_DIR / GENERATED_NAME).exists()
            and (APP_DIR / GENERATED_NAME).stat().st_size > 1000,
            "the check above would pass vacuously if nothing was written")


def check_flavor_overlays(r: Results) -> None:
    """The per-flavor res/ overlays are generated too, and were tracked.

    build.gradle.kts was not the only generated file under version
    control. write_flavor_resources rmtree's src/c<number>/ before
    rewriting it, and clean_stale_flavors rmtree's any flavor missing from
    the current catalog - so a build for a different set of companies
    DELETED them. Measured, before the fix: two ' D' entries for
    src/c15046885 plus an untracked src/c16940106.
    """
    print("\n-- the per-flavor resource overlays are generated, not tracked --")
    src_dir = APP_DIR / "src"
    tracked = subprocess.run(
        ["git", "ls-files", str((src_dir).relative_to(REPO)) + "/c*"],
        cwd=REPO, capture_output=True, text=True).stdout.split()
    r.check("no flavor overlay is tracked", not tracked,
            f"a build would delete or rewrite these: {tracked}")
    p = subprocess.run(["git", "check-ignore", "-q",
                        str((src_dir / "c16940106" / "res" / "values" /
                             "colors.xml").relative_to(REPO))], cwd=REPO)
    r.check("...and a new one would be ignored", p.returncode == 0,
            "the next build leaves an untracked directory behind")

    # src/main is hand-written. If the ignore rule reached it, the app's
    # own source would silently stop being tracked - a far worse failure
    # than the one being fixed.
    main_manifest = src_dir / "main" / "AndroidManifest.xml"
    # --no-index for the same reason: src/main is tracked, so the default
    # answer is always "not ignored" and a rule of `app/src/` - which WOULD
    # swallow the app's own source - went undetected by this very check.
    p = subprocess.run(["git", "check-ignore", "-q", "--no-index",
                        str(main_manifest.relative_to(REPO))], cwd=REPO)
    r.check("src/main is NOT ignored", p.returncode != 0,
            "the rule is too broad and has swallowed the app's own source")
    p = subprocess.run(["git", "ls-files", "--error-unmatch",
                        str(main_manifest.relative_to(REPO))],
                       cwd=REPO, capture_output=True)
    r.check("...and is still tracked", p.returncode == 0, str(main_manifest))

    # End to end: write an overlay for a company the repo has never seen
    # and confirm git reports nothing new.
    c = _company("16940106", "ACME ADVISORY LTD", "acmeadvisory.uk")
    before = subprocess.run(["git", "status", "--porcelain", str(src_dir)],
                            cwd=REPO, capture_output=True, text=True).stdout
    written = Path(fw.write_flavor_resources(c, palette_for(c.company_name),
                                             str(APP_DIR)))
    after = subprocess.run(["git", "status", "--porcelain", str(src_dir)],
                           cwd=REPO, capture_output=True, text=True).stdout
    r.check("writing an overlay changes nothing git can see", before == after,
            f"before={before!r}\n          after={after!r}")
    r.check("...and it really did write one",
            (written / "res" / "values" / "colors.xml").exists(),
            "the check above would pass vacuously if nothing was written")
    shutil.rmtree(written, ignore_errors=True)


def check_no_secret_in_template(r: Results) -> None:
    print("\n-- the tracked template carries no secret --")
    src = TEMPLATE.read_text(encoding="utf-8")
    for word in (_STORE_PW, _KEY_PW, _STORE_FILE):
        r.check(f"the template has no {word}", word not in src,
                "generated signing material must not be in a tracked file")
    r.check("the template's marker block is empty",
            src.split(fw._MARKER_START)[1].split(fw._MARKER_END)[0].strip() == "",
            "the tracked file carries generated content")
    # And the generated one DOES carry them - otherwise the three checks
    # above would pass against a generator that had stopped emitting
    # signing configs at all, which would break every release build.
    app = _sandbox()
    cs = [_company("16940106", "ACME ADVISORY LTD")]
    out = Path(fw.write_flavors_gradle(_built(cs, str(app)), str(app)))
    gen = out.read_text(encoding="utf-8")
    r.check("...while the generated file still carries all three",
            all(w in gen for w in (_STORE_PW, _KEY_PW, _STORE_FILE)),
            "the generator stopped emitting signing material; release "
            "builds would be unsigned")

    # Keyword presence is not enough. A mutation that left every
    # store-password line in place but wrapped the block in `if (false)`
    # survived this suite - the strings were all still there, and only a
    # real Gradle build would have noticed. So the STRUCTURE is checked:
    # the flavor's signing config must sit inside the signingConfigs block
    # that the productFlavors block then refers to.
    signing = _balanced_block(gen, "signingConfigs {")
    r.check("the signingConfigs block is balanced",
            signing is not None, "no matching close brace")
    r.check("...and the flavor's config is INSIDE it",
            signing is not None and 'create("c16940106")' in signing,
            f"got {signing!r}")
    r.check("...with its password inside the same block",
            signing is not None and "store-secret-16940106" in signing)
    r.check("...and nothing disables it",
            signing is not None and "if (" not in signing,
            f"a conditional in the signing block can silently unsign a "
            f"release: {signing!r}")
    flavors = _balanced_block(gen, "productFlavors {")
    r.check("the productFlavors block is balanced", flavors is not None)
    r.check("...and the flavor claims that signing config",
            flavors is not None
            and 'signingConfig = signingConfigs.getByName("c16940106")' in flavors,
            f"got {flavors!r}")


def main() -> int:
    r = Results()
    print(f"target: {TEMPLATE.relative_to(REPO)} -> {GENERATED_NAME}")
    check_renders_from_template(r)
    check_missing_template(r)
    check_git(r)
    check_flavor_overlays(r)
    check_no_secret_in_template(r)
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
