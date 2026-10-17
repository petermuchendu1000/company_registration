"""
Store screenshots: that they are of the app, and of the right app.

This pipeline used to draw its screenshots. assets.py rendered one hardcoded
set of shift-journal screens — a timer, a "Log" tab, a calculator — for every
company regardless of archetype, so a mileage app's Play listing showed a
shift timer and a navigation bar the app does not have. Play rejects listings
whose screenshots misrepresent the app, and that is what those were.

They are now captured from the built app: ListingScreenshotTest drives the
real MainActivity under Robolectric and writes the PNGs plus a record of
which build they came from. These tests cover the Python side — collecting
them into a listing, and the gate that refuses to publish a listing whose
screenshots are short, unattributed, or of somebody else's app.

The Kotlin renderer has its own checks in-process (it asserts the pixel size
of every frame it writes). What cannot be tested here is whether the image
looks right; that is what reading the rendered PNGs is for.

    python3 test_screenshots.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apps.generator import archetypes, assets, policy_gate, screenshots  # noqa: E402
from apps.generator.catalog import Company  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cf000001010100b5fbffef0000000049454e44ae426082"
)


def _rendered(flavor_dir: Path, *, archetype="quote", company_number="13510663",
              groups=tuple(screenshots.GROUPS.values()), count=None) -> Path:
    """A flavor's render output, as the Kotlin test leaves it."""
    n = screenshots.SHOTS_PER_GROUP if count is None else count
    flavor_dir.mkdir(parents=True, exist_ok=True)
    for prefix in groups:
        for i in range(1, n + 1):
            (flavor_dir / f"{prefix}-{i}.png").write_bytes(PNG)
        (flavor_dir / f"{prefix}.json").write_text(json.dumps({
            "archetype": archetype,
            "application_id": "uk.example.app",
            "company_number": company_number,
            "shots": [{"file": f"{prefix}-{i}.png", "caption": f"screen {i}"}
                      for i in range(1, n + 1)],
        }))
    return flavor_dir


# ── collecting what was rendered ────────────────────────────────────────────

def test_collect_copies_every_size_into_the_listing() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d) / "app"
        _rendered(screenshots.shots_dir(app, "c1"))
        graphics = Path(d) / "out" / "graphics"
        groups, prov = screenshots.collect(app, "c1", graphics)

        assert set(groups) == {"phone", "tablet7", "tablet10"}, groups
        for key, paths in groups.items():
            assert len(paths) == screenshots.SHOTS_PER_GROUP, (key, paths)
            for rel in paths:
                assert (graphics.parent / rel).exists(), rel
        assert prov["archetype"] == "quote", prov
    print("  OK  collect copies all three sizes and reports the provenance")


def test_collect_omits_a_group_that_did_not_finish() -> None:
    """
    Half a set is worse than none: the listing would name files Play then
    fails to find, and the gap would not surface until upload.
    """
    with tempfile.TemporaryDirectory() as d:
        app = Path(d) / "app"
        flavor_dir = _rendered(screenshots.shots_dir(app, "c1"))
        (flavor_dir / "tablet10-screenshot-3.png").unlink()
        (flavor_dir / "tablet10-screenshot-4.png").unlink()

        groups, _ = screenshots.collect(app, "c1", Path(d) / "out" / "graphics")
        assert "tablet10" not in groups, groups
        assert set(groups) == {"phone", "tablet7"}, groups
    print("  OK  a partially rendered size is left out of the listing")


def test_collect_survives_nothing_having_been_rendered() -> None:
    with tempfile.TemporaryDirectory() as d:
        groups, prov = screenshots.collect(Path(d) / "app", "c1",
                                           Path(d) / "out" / "graphics")
        assert groups == {} and prov == {}, (groups, prov)
    print("  OK  a missing render is reported, not raised")


def test_gradle_task_names_match_the_flavors() -> None:
    assert screenshots.gradle_tasks(["c15046885", "c02591663"]) == [
        ":app:testC15046885DebugUnitTest",
        ":app:testC02591663DebugUnitTest",
    ]
    print("  OK  the render task is named per flavor")


# ── the listing describes what is on disk ───────────────────────────────────

def test_listing_names_the_screenshots_that_were_collected() -> None:
    payload = assets.listing_payload({
        "display_name": "Bright Spark", "archetype": "quote",
        "screenshots": {"phone": ["graphics/phone-screenshot-1.png"]},
        "screenshot_provenance": {"archetype": "quote"},
    })
    g = payload["graphics"]
    assert g["phone_screenshots"] == ["graphics/phone-screenshot-1.png"], g
    assert g["tablet_7inch_screenshots"] == [], g
    assert g["tablet_10inch_screenshots"] == [], g
    print("  OK  the listing lists the files that exist, not a fixed count")


def test_listing_uses_the_company_archetype() -> None:
    """
    generate_all built its metadata without the archetype, so listing_payload
    fell through to the default on every company: mileage and quote apps
    shipped store pages describing a shift journal.
    """
    quote = assets.listing_payload({"display_name": "X", "archetype": "quote"})
    mileage = assets.listing_payload({"display_name": "X", "archetype": "mileage"})
    assert quote["graphics"]["screenshot_screens"] != \
        mileage["graphics"]["screenshot_screens"], "both listings describe the same screens"
    assert "Estimate" in quote["graphics"]["screenshot_screens"][0], quote
    print("  OK  the listing describes the company's own archetype")


def test_generate_all_carries_the_archetype_through() -> None:
    """The gap was between generate_all's metadata and listing_payload."""
    with tempfile.TemporaryDirectory() as d:
        app = Path(d) / "app"
        _rendered(screenshots.shots_dir(app, "c1"), archetype="mileage",
                  company_number="02591663")
        out = Path(d) / "out"
        assets.generate_all(
            {"display_name": "Dock Haulage", "company_number": "02591663",
             "application_id": "uk.dock.app", "archetype": "mileage",
             "sic_codes": "49410"},
            None, None, out, "#0E6B5E", app_dir=app, flavor="c1")
        listing = json.loads((out / "listing" / "play_listing.json").read_text())
        g = listing["graphics"]
        assert g["screenshot_provenance"]["archetype"] == "mileage", g
        assert "Journeys" in " ".join(g["screenshot_screens"]), g
        assert len(g["tablet_10inch_screenshots"]) == screenshots.SHOTS_PER_GROUP, g
    print("  OK  generate_all passes the archetype and the shots to the listing")


def test_every_archetype_declares_one_caption_per_shot() -> None:
    for a in archetypes.ALL:
        assert len(a.screenshot_screens) == screenshots.SHOTS_PER_GROUP, \
            f"{a.id} declares {len(a.screenshot_screens)} captions for " \
            f"{screenshots.SHOTS_PER_GROUP} screenshots"
    print(f"  OK  all {len(archetypes.ALL)} archetypes caption every screenshot")


def test_nothing_draws_screenshots_any_more() -> None:
    """
    A regression guard with teeth: the drawn screenshots were the defect, and
    a helper that renders a plausible-looking app screen is exactly the thing
    someone reaches for again.
    """
    source = Path(__file__).parent / "apps" / "generator" / "assets.py"
    text = source.read_text()
    assert "make_phone_screenshot" not in text, \
        "assets.py draws screenshots again — they must be captured from the app"
    print("  OK  assets.py no longer draws screenshots")


# ── the gate ────────────────────────────────────────────────────────────────

def _listing(graphics: dict) -> dict:
    return {"graphics": graphics}


def _company(cn="13510663", sic="43210") -> Company:
    return Company(cn, "BRIGHT SPARK ELECTRICAL LTD", sic,
                   "bright-spark.online", "dev@bright-spark.online", "quote")


def _full_graphics(app: Path, *, archetype="quote", company_number="13510663") -> dict:
    (app / "graphics").mkdir(parents=True, exist_ok=True)
    out = {}
    for key, prefix in (("phone_screenshots", "phone-screenshot"),
                        ("tablet_7inch_screenshots", "tablet7-screenshot"),
                        ("tablet_10inch_screenshots", "tablet10-screenshot")):
        paths = []
        for i in range(1, screenshots.SHOTS_PER_GROUP + 1):
            (app / "graphics" / f"{prefix}-{i}.png").write_bytes(PNG)
            paths.append(f"graphics/{prefix}-{i}.png")
        out[key] = paths
    out["screenshot_screens"] = list(archetypes.get(archetype).screenshot_screens)
    out["screenshot_provenance"] = {
        "archetype": archetype, "company_number": company_number,
        "shots": [{"file": f"phone-screenshot-{i}.png", "caption": f"s{i}"}
                  for i in range(1, screenshots.SHOTS_PER_GROUP + 1)],
    }
    return out


def _codes(findings) -> set[str]:
    return {f.code for f in findings if f.severity == "blocker"}


def test_gate_passes_a_complete_matching_set() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app)
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert not _codes(found), [f.as_dict() for f in found]
    print("  OK  a complete, correctly attributed set passes")


def test_gate_blocks_a_short_set() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app)
        for i in (3, 4):
            (app / "graphics" / f"tablet7-screenshot-{i}.png").unlink()
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert "missing_tablet_7inch_screenshots" in _codes(found), _codes(found)
    print("  OK  a size short of Play's minimum blocks the publish")


def test_gate_blocks_screenshots_of_a_different_product() -> None:
    """The original defect, now caught: a quote app with shift screenshots."""
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app, archetype="shift")
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert "screenshot_archetype_mismatch" in _codes(found), _codes(found)
    print("  OK  screenshots from another archetype block the publish")


def test_gate_blocks_screenshots_of_a_different_company() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app, company_number="99999999")
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert "screenshot_company_mismatch" in _codes(found), _codes(found)
    print("  OK  another company's screenshots block the publish")


def test_gate_blocks_unattributed_screenshots() -> None:
    """Drawn screenshots carry no provenance, so this is the backstop."""
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app)
        g["screenshot_provenance"] = {}
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert "unattributed_screenshots" in _codes(found), _codes(found)
    print("  OK  screenshots with no record of their build block the publish")


def test_gate_blocks_a_listing_that_only_claims_to_have_them() -> None:
    """The paths are in the manifest; the files are not on disk."""
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app)
        shutil.rmtree(app / "graphics")
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        assert {"missing_phone_screenshots", "missing_tablet_7inch_screenshots",
                "missing_tablet_10inch_screenshots"} <= _codes(found), _codes(found)
    print("  OK  a listing naming files that are not there blocks the publish")


def test_gate_warns_when_captions_and_shots_disagree() -> None:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        g = _full_graphics(app)
        g["screenshot_screens"] = g["screenshot_screens"][:2]
        found = policy_gate._check_screenshots(app, _listing(g), _company(), "13510663")
        warnings = {f.code for f in found if f.severity == "warning"}
        assert "screenshot_caption_drift" in warnings, warnings
    print("  OK  captions drifting from the rendered set is reported")


def main() -> int:
    tests = [
        test_collect_copies_every_size_into_the_listing,
        test_collect_omits_a_group_that_did_not_finish,
        test_collect_survives_nothing_having_been_rendered,
        test_gradle_task_names_match_the_flavors,
        test_listing_names_the_screenshots_that_were_collected,
        test_listing_uses_the_company_archetype,
        test_generate_all_carries_the_archetype_through,
        test_every_archetype_declares_one_caption_per_shot,
        test_nothing_draws_screenshots_any_more,
        test_gate_passes_a_complete_matching_set,
        test_gate_blocks_a_short_set,
        test_gate_blocks_screenshots_of_a_different_product,
        test_gate_blocks_screenshots_of_a_different_company,
        test_gate_blocks_unattributed_screenshots,
        test_gate_blocks_a_listing_that_only_claims_to_have_them,
        test_gate_warns_when_captions_and_shots_disagree,
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
