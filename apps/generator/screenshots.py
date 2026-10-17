"""
Store screenshots, rendered from the app itself.

Google Play rejects a listing whose screenshots do not represent the app, and
for a long time this pipeline shipped drawings: assets.py drew one hardcoded
set of shift-journal screens — a timer, a "Log" tab, a calculator — for every
company, whatever archetype it was built from. A mileage app went to Play with
pictures of a shift timer and a navigation bar it does not have.

Drawing them more carefully does not fix that. Any second rendering of the UI
drifts from the real one the moment either side changes, and the drift is
invisible until a reviewer notices. So the screenshots are taken from the
built app: `ListingScreenshotTest` (app/src/test/.../shots/) drives the real
MainActivity under Robolectric with NATIVE graphics, seeds the same
SharedPreferences the app reads so the screens have believable records in
them, clicks through the bottom bar and captures each composed frame at the
three sizes Play asks for.

This module is the Python side of that: it runs the Gradle task per flavor and
hands back where the PNGs landed. Everything about what is drawn lives in the
Kotlin test, next to the UI it photographs.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

#: Play's three mandatory sizes, and the manifest key each group belongs
#: under. The sizes themselves are asserted in the Kotlin test, which is where
#: a mis-sized render would actually be produced.
GROUPS: dict[str, str] = {
    "phone": "phone-screenshot",
    "tablet7": "tablet7-screenshot",
    "tablet10": "tablet10-screenshot",
}

#: Matches ListingScreenshotTest.plan(): the archetype's own two tabs, then
#: Reference and Contact. Kept here so the Python side can check the render
#: produced a full set without parsing Kotlin.
SHOTS_PER_GROUP = 4

_TEST_FILTER = "*ListingScreenshotTest*"


def shots_dir(app_dir: str | Path, flavor: str) -> Path:
    """Where build.gradle.kts tells the test to write this flavor's PNGs."""
    return Path(app_dir) / "build" / "listing-shots" / flavor


def gradle_tasks(flavors: list[str], variant: str = "Debug") -> list[str]:
    """
    The unit-test task per flavor.

    Debug by default even when the pipeline builds a release bundle: the
    screenshots are of the UI, which is identical, and a release unit test
    would run against minified, resource-shrunk code for no gain.
    """
    return [f":app:test{f.capitalize()}{variant}UnitTest" for f in flavors]


def render(
    launcher: list[str],
    template_root: str | Path,
    flavors: list[str],
    *,
    variant: str = "Debug",
) -> int:
    """
    Render every flavor's screenshots in one Gradle invocation.

    Returns Gradle's exit code. The caller decides what a failure means;
    `collect` reports separately on what actually reached disk, because a
    green Gradle run with no PNGs is a different fault from a red one.
    """
    if not flavors:
        return 0
    tasks = gradle_tasks(flavors, variant)
    cmd = [
        *launcher, "--no-daemon", "--console=plain",
        *tasks, "--tests", _TEST_FILTER,
    ]
    print(f"\n  Screenshots: {len(flavors)} flavor(s) x "
          f"{len(GROUPS) * SHOTS_PER_GROUP} shots")
    t0 = time.time()
    res = subprocess.run(cmd, cwd=str(template_root))
    print(f"  Screenshots exit={res.returncode} in {time.time() - t0:.1f}s")
    return res.returncode


def collect(app_dir: str | Path, flavor: str,
            graphics_dir: Path) -> tuple[dict[str, list[str]], dict]:
    """
    Copy a flavor's rendered PNGs into its listing graphics/ folder.

    Returns `(paths_by_group, provenance)`. Only groups that rendered in full
    are listed: a partial group is left out rather than half-promised, so the
    manifest never points Play at a file that is not there and the policy
    gate turns the gap into a blocked publish.

    `provenance` is what the renderer recorded about itself — the archetype,
    application id and company number of the build it photographed. The gate
    compares it against the company being published, which is the only thing
    standing between a listing and another app's screenshots.
    """
    src = shots_dir(app_dir, flavor)
    graphics_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[str]] = {}
    provenance: dict = {}
    for key, prefix in GROUPS.items():
        paths = []
        for i in range(1, SHOTS_PER_GROUP + 1):
            png = src / f"{prefix}-{i}.png"
            if not png.exists():
                break
            dest = graphics_dir / png.name
            dest.write_bytes(png.read_bytes())
            paths.append(f"graphics/{dest.name}")
        if len(paths) == SHOTS_PER_GROUP:
            out[key] = paths
            manifest = src / f"{prefix}.json"
            if manifest.exists() and not provenance:
                provenance = json.loads(manifest.read_text(encoding="utf-8"))
        elif paths:
            print(f"  WARNING: {flavor} rendered {len(paths)}/{SHOTS_PER_GROUP} "
                  f"{key} screenshots; the group is omitted from the listing")
    if not out:
        print(f"  WARNING: no screenshots for {flavor}. Run the "
              f"ListingScreenshotTest task before generating assets "
              f"(expected them under {src}).")
    return out, provenance
