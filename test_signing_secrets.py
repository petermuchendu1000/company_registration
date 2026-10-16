"""
Keystore passwords must not reach a tracked file.

apps/template-shift-journal/app/build.gradle.kts is in version control, and
write_flavors_gradle used to emit the signing block with the passwords as
string literals:

    storePassword = "<32 hex characters>"
    keyPassword   = "<the same 32>"

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


def _committed_passwords() -> list[str]:
    """
    Every signing password that has ever been committed to this file.

    Read out of git history rather than hardcoded. Writing the value into
    this test would put it back into a tracked file, and it would only ever
    cover the one password someone happened to notice.
    """
    log = _git("log", "--format=%H", "--follow", "--",
               "apps/template-shift-journal/app/build.gradle.kts")
    if log.returncode != 0:
        return []
    found: list[str] = []
    for sha in log.stdout.split():
        blob = _git("show", f"{sha}:apps/template-shift-journal/app/build.gradle.kts")
        if blob.returncode != 0:
            continue
        for match in re.findall(r'(?:storePassword|keyPassword)\s*=\s*"([^"]+)"', blob.stdout):
            if match not in found:
                found.append(match)
    return found


def test_no_historical_password_survives_in_the_working_tree() -> None:
    leaked = _committed_passwords()
    if not leaked:
        print("  SKIP  no committed password found in history (shallow clone?)")
        return
    still_there = [p for p in leaked if _git("grep", "-n", p).stdout.strip()]
    assert not still_there, f"a committed password is still in a tracked file: {still_there}"
    print(f"  OK  {len(leaked)} password(s) found in history, none in tracked files")


# ── rotation ─────────────────────────────────────────────────────────────────

def _with_isolated_rotations(fn):
    """
    Run fn() against a throwaway rotations file.

    The real one records a rotation that actually happened; a test must not
    bump a generation in it, because the password for the live keystore is
    derived from that number and nothing else can recover it.
    """
    from apps.generator import keystore_mint as km
    original = km._ROTATIONS_PATH
    with tempfile.TemporaryDirectory() as d:
        km._ROTATIONS_PATH = str(Path(d) / "rotations.json")
        try:
            return fn(km)
        finally:
            km._ROTATIONS_PATH = original


def test_generation_one_keeps_the_original_derivation() -> None:
    """Rotation must not silently change every untouched company's password."""
    def check(km):
        import hashlib as h, hmac as hm
        master = km._load_or_create_master()
        expected = hm.new(master, b"19998877", h.sha256).hexdigest()[:32]
        assert km.password_for("19998877") == expected, "generation 1 derivation changed"
        assert km.generation("19998877") == 1
    _with_isolated_rotations(check)
    print("  OK  an unrotated company still derives the password it always had")


def test_rotating_changes_the_password() -> None:
    def check(km):
        before = km.password_for("19998877")
        km._save_rotations({"19998877": 2})
        after = km.password_for("19998877")
        assert after != before, "the generation did not change the password"
        assert len(after) == 32, after
        # ...and it is reproducible, or the keystore becomes unopenable.
        assert km.password_for("19998877") == after, "derivation is not deterministic"
    _with_isolated_rotations(check)
    print("  OK  bumping a generation yields a different, reproducible password")


def test_rotation_is_per_company() -> None:
    def check(km):
        other_before = km.password_for("13510663")
        km._save_rotations({"19998877": 2})
        assert km.password_for("13510663") == other_before, \
            "rotating one company changed another's password"
    _with_isolated_rotations(check)
    print("  OK  rotating one company leaves every other password alone")


def test_the_committed_password_opens_nothing() -> None:
    """The whole point of the rotation: that pair must be dead."""
    ks_dir = TEMPLATE_APP / "keystores"
    if not shutil.which("keytool"):
        print("  SKIP  keytool not on PATH")
        return
    leaked = _committed_passwords()
    if not leaked:
        print("  SKIP  no committed password found in history (shallow clone?)")
        return
    opened = []
    for store in sorted(ks_dir.rglob("*.jks")):
        for password in leaked:
            rc = subprocess.run(
                ["keytool", "-list", "-keystore", str(store),
                 "-storepass", password, "-storetype", "PKCS12"],
                capture_output=True, timeout=30,
            ).returncode
            if rc == 0:
                opened.append(str(store.relative_to(ROOT)))
    assert not opened, f"a committed password still opens: {opened}"
    print(f"  OK  none of the {len(leaked)} historical password(s) open any keystore")


def test_rotations_file_is_git_ignored() -> None:
    target = ROOT / "apps" / "generator" / ".keystore_rotations.json"
    created = not target.exists()
    if created:
        target.write_text("{}\n", encoding="utf-8")
    try:
        ignored = _git("check-ignore", "-q",
                       str(target.relative_to(ROOT))).returncode == 0
    finally:
        if created:
            target.unlink(missing_ok=True)
    assert ignored, ".keystore_rotations.json is NOT git-ignored"
    print("  OK  the rotations file is git-ignored")


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
        test_no_historical_password_survives_in_the_working_tree,
        test_generation_one_keeps_the_original_derivation,
        test_rotating_changes_the_password,
        test_rotation_is_per_company,
        test_the_committed_password_opens_nothing,
        test_rotations_file_is_git_ignored,
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
