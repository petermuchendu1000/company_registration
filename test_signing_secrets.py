"""
Keystore passwords must not reach a tracked file.

apps/template-shift-journal/app/build.gradle.kts is in version control, and
write_flavors_gradle used to emit the signing block with the passwords as
string literals:

    storePassword = "474c4992049490b5e1cc76a7430659f4"
    keyPassword   = "474c4992049490b5e1cc76a7430659f4"

The .jks files themselves are git-ignored, so an attacker with the repository
had the password but not the key. That is still one factor of two handed over,
and a password committed once stays in the history after it is removed.

The passwords now go to app/keystore.properties, which git ignores, and the
build file calls signingSecret(flavor, which) to read them back — from the
environment first, so CI needs no file on disk.

Fixing the committed file alone would have been pointless: the generator
rewrites that region on every build and would have put the literals straight
back. These tests exercise the generator, not the file it happened to leave
behind.

    python test_signing_secrets.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from apps.generator.flavor_writer import (          # noqa: E402
    BuiltFlavor, KEYSTORE_PROPERTIES, write_flavors_gradle,
)
from apps.generator.catalog import Company          # noqa: E402
from apps.generator.keystore_mint import Keystore   # noqa: E402

#: Distinctive enough that finding it anywhere is unambiguous.
SECRET = "deadbeefcafe0123456789abcdef0000"

TEMPLATE_APP = ROOT / "apps" / "template-shift-journal" / "app"
MARKER_START = "// >>> GENERATED FLAVORS START"
MARKER_END = "// <<< GENERATED FLAVORS END"


def _company() -> Company:
    return Company(
        company_number="19998877",
        company_name="HARBOUR COURIERS LTD",
        sic_codes="53202",
        domain="harbourcouriers.online",
        support_email="dev@harbourcouriers.online",
        archetype="shift",
        address="12 Dock Road, Liverpool",
    )


def _built(app_dir: Path) -> BuiltFlavor:
    ks_dir = app_dir / "keystores"
    ks_dir.mkdir(parents=True, exist_ok=True)
    ks_path = ks_dir / "19998877.jks"
    ks_path.write_bytes(b"not-a-real-keystore")
    try:
        from apps.generator.palette import palette_for   # type: ignore
        palette = palette_for(_company())
    except Exception:
        palette = None
    return BuiltFlavor(
        company=_company(),
        palette=palette,
        keystore=Keystore(path=str(ks_path), store_password=SECRET,
                          key_alias="upload", key_password=SECRET),
        flavor_dir=str(app_dir / "src" / "c19998877"),
    )


def _app_dir(tmp: Path) -> Path:
    """A throwaway copy of the template's build file, markers and all."""
    app = tmp / "app"
    app.mkdir(parents=True)
    shutil.copy(TEMPLATE_APP / "build.gradle.kts", app / "build.gradle.kts")
    return app


def _run(tmp: Path) -> tuple[str, Path]:
    app = _app_dir(tmp)
    write_flavors_gradle([_built(app)], str(app))
    return (app / "build.gradle.kts").read_text(encoding="utf-8"), app


# ── the generator ────────────────────────────────────────────────────────────

def test_no_password_in_the_tracked_build_file() -> None:
    with tempfile.TemporaryDirectory() as d:
        gradle, _ = _run(Path(d))
    assert SECRET not in gradle, "the password was written into build.gradle.kts"
    print("  OK  the generated build file contains no password")


def test_no_quoted_password_assignment_at_all() -> None:
    """Catches a literal under any name, not just the one this test injects."""
    with tempfile.TemporaryDirectory() as d:
        gradle, _ = _run(Path(d))
    hit = re.search(r'(storePassword|keyPassword)\s*=\s*"', gradle)
    assert hit is None, f"a quoted password assignment is back: {hit.group(0)!r}"
    print("  OK  no storePassword/keyPassword is assigned a string literal")


def test_the_build_file_reads_the_secret_instead() -> None:
    with tempfile.TemporaryDirectory() as d:
        gradle, _ = _run(Path(d))
    for which in ("storePassword", "keyPassword"):
        expected = f'{which} = signingSecret("c19998877", "{which}")'
        assert expected in gradle, f"missing: {expected}"
    print("  OK  the signing config resolves both passwords through signingSecret()")


def test_the_password_lands_in_the_ignored_properties_file() -> None:
    with tempfile.TemporaryDirectory() as d:
        _, app = _run(Path(d))
        props = (app / KEYSTORE_PROPERTIES).read_text(encoding="utf-8")
        mode = (app / KEYSTORE_PROPERTIES).stat().st_mode & 0o777
    assert f"c19998877.storePassword={SECRET}" in props, props
    assert f"c19998877.keyPassword={SECRET}" in props, props
    assert mode == 0o600, f"properties file is mode {mode:o}, not 600"
    print("  OK  the passwords are written to keystore.properties, mode 600")


def test_properties_file_is_rewritten_not_appended() -> None:
    """A flavor that is no longer built must not leave its password behind."""
    with tempfile.TemporaryDirectory() as d:
        app = _app_dir(Path(d))
        stale = app / KEYSTORE_PROPERTIES
        stale.write_text("c00000001.storePassword=stale-secret\n", encoding="utf-8")
        write_flavors_gradle([_built(app)], str(app))
        props = stale.read_text(encoding="utf-8")
    assert "stale-secret" not in props, "a password from a previous run survived"
    print("  OK  the properties file is rewritten, so a dropped flavor's password goes")


# ── the repository ───────────────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, timeout=15)


def test_keystore_properties_is_git_ignored() -> None:
    target = TEMPLATE_APP / KEYSTORE_PROPERTIES
    created = not target.exists()
    if created:
        target.write_text("# probe\n", encoding="utf-8")
    try:
        ignored = _git("check-ignore", "-q",
                       str(target.relative_to(ROOT))).returncode == 0
    finally:
        if created:
            target.unlink(missing_ok=True)
    assert ignored, f"{target.relative_to(ROOT)} is NOT git-ignored"
    print("  OK  keystore.properties is git-ignored")


def test_keystores_themselves_are_git_ignored() -> None:
    ks_dir = TEMPLATE_APP / "keystores"
    stores = sorted(ks_dir.glob("*.jks")) if ks_dir.is_dir() else []
    for store in stores:
        assert _git("check-ignore", "-q", str(store.relative_to(ROOT))).returncode == 0, \
            f"{store.relative_to(ROOT)} is NOT git-ignored"
    assert not _git("ls-files", "--", "*.jks").stdout.strip(), "a .jks is tracked"
    print(f"  OK  {len(stores)} keystore(s) present, none tracked, all ignored")


def test_master_secret_is_git_ignored() -> None:
    secret = ROOT / "apps" / "generator" / ".master_secret"
    assert _git("check-ignore", "-q", ".master_secret" if not secret.exists()
                else str(secret.relative_to(ROOT))).returncode == 0, \
        ".master_secret is NOT git-ignored — every password derives from it"
    print("  OK  .master_secret is git-ignored")


def test_no_literal_password_in_the_working_tree() -> None:
    """The committed one, specifically — it is the reason this file exists."""
    out = _git("grep", "-n", "474c4992049490b5e1cc76a7430659f4")
    assert not out.stdout.strip(), f"still present:\n{out.stdout}"
    print("  OK  the previously committed password is gone from tracked files")


def main() -> int:
    tests = [
        test_no_password_in_the_tracked_build_file,
        test_no_quoted_password_assignment_at_all,
        test_the_build_file_reads_the_secret_instead,
        test_the_password_lands_in_the_ignored_properties_file,
        test_properties_file_is_rewritten_not_appended,
        test_keystore_properties_is_git_ignored,
        test_keystores_themselves_are_git_ignored,
        test_master_secret_is_git_ignored,
        test_no_literal_password_in_the_working_tree,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
