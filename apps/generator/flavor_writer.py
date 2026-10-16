"""Emit Gradle flavors + per-flavor Android resource overlays.

Writes three things:
  1. {app}/flavors.gradle.kts          — one productFlavor + signingConfig per company
  2. {app}/src/c{cn}/res/values/*.xml  — per-flavor colors + strings
  3. {app}/keystores/{cn}.jks          — via keystore_mint (caller's responsibility)

The static build.gradle.kts applies flavors.gradle.kts via `apply(from=...)`.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .brand_synth import Palette, colors_xml, palette_for, strings_xml
from .catalog import Company
from .keystore_mint import Keystore
from . import archetypes
from . import sic_map as _sic_map

_ROOT = Path(__file__).parent.parent.parent


def _load_version(company_number: str, company_name: str = "") -> dict:
    """Read version.json for this company. Returns defaults if not found."""
    import company_paths
    ver_file = company_paths.company_dir(company_number, company_name) / "app" / "version.json"
    if ver_file.exists():
        try:
            data = json.loads(ver_file.read_text(encoding="utf-8"))
            return {
                "version_code": int(data.get("version_code", 1)),
                "version_name": str(data.get("version_name", "1.0")),
            }
        except Exception:
            pass
    return {"version_code": 1, "version_name": "1.0"}


def _load_privacy_url(company_number: str, company_name: str = "") -> str:
    """Read privacy_policy_url from the company's manifest.json if it exists."""
    import company_paths
    candidates = [
        company_paths.company_dir(company_number, company_name) / "app" / "manifest.json",
        _ROOT / "pipeline_output" / "apps" / company_number / "manifest.json",  # legacy
    ]
    for manifest in candidates:
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                return data.get("privacy_policy_url") or ""
            except Exception:
                pass
    return ""


@dataclass(frozen=True)
class BuiltFlavor:
    company: Company
    palette: Palette
    keystore: Keystore
    flavor_dir: str


def _gradle_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def _bc_str(s: str) -> str:
    """Produce the Gradle buildConfigField third-arg for a String value.
    Applies two levels of escaping: Python→Java literal→Kotlin literal.
    Handles strings containing quotes, backslashes, JSON, etc.
    """
    java_content = s.replace("\\", "\\\\").replace('"', '\\"')
    java_expr = f'"{java_content}"'
    kotlin = java_expr.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{kotlin}"'


def archetype_for(company: Company) -> archetypes.Archetype:
    """The archetype this company's app is built from.

    Derived from the SIC code rather than read off `company.archetype`, so a
    row loaded before an archetype existed starts building the right product
    as soon as the registry learns about it. catalog.load_companies fills the
    field from the same table, so the two agree.
    """
    return archetypes.for_sic(company.sic_codes)


def _sic_role_vocab(company: Company) -> dict[str, str]:
    """Role-vocabulary strings baked into BuildConfig, within the archetype."""
    return archetypes.vocab_for(archetype_for(company), company.sic_codes)


def write_flavor_resources(company: Company, palette: Palette, app_dir: str) -> str:
    """Write per-flavor res overlays. Returns the flavor source-set dir."""
    flavor = company.flavor
    flavor_dir = os.path.join(app_dir, "src", flavor)
    # Wipe any prior flavor dir so stale files from earlier catalogs don't leak in.
    if os.path.isdir(flavor_dir):
        shutil.rmtree(flavor_dir)
    values_dir = os.path.join(flavor_dir, "res", "values")
    os.makedirs(values_dir, exist_ok=True)

    with open(os.path.join(values_dir, "colors.xml"), "w", encoding="utf-8") as f:
        f.write(colors_xml(palette))
    with open(os.path.join(values_dir, "strings.xml"), "w", encoding="utf-8") as f:
        f.write(strings_xml(company.display_name))
    return flavor_dir


_MARKER_START = "// >>> GENERATED FLAVORS START"
_MARKER_END = "// <<< GENERATED FLAVORS END"


#: Read by signingSecret() in build.gradle.kts. Git-ignored: it holds the
#: keystore passwords that used to be baked into the tracked build file.
KEYSTORE_PROPERTIES = "keystore.properties"


def write_keystore_properties(built: list[BuiltFlavor], app_dir: str) -> str:
    """
    Write the per-flavor signing passwords next to the build file.

    Rewritten in full on every run, so a flavor that is no longer built does
    not leave its password behind. Written 0600: on a shared host the whole
    point is undone if the file is world-readable.
    """
    path = os.path.join(app_dir, KEYSTORE_PROPERTIES)
    lines = [
        "# Signing passwords, written by apps/generator/flavor_writer.py.",
        "# Git-ignored, and regenerated on every build — do not edit by hand,",
        "# and do not commit. Deriving them again needs apps/generator/.master_secret.",
        "",
    ]
    for b in built:
        ks = b.keystore
        lines += [
            f"{b.company.flavor}.storePassword={ks.store_password}",
            f"{b.company.flavor}.keyPassword={ks.key_password}",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass    # Windows and some mounts refuse; the git-ignore still holds
    return path


def write_flavors_gradle(built: list[BuiltFlavor], app_dir: str) -> str:
    """Splice flavor + signingConfig blocks into build.gradle.kts between markers.

    The build.gradle.kts file must contain a pair of marker lines; everything
    between them is replaced on every run.
    """
    build_file = os.path.join(app_dir, "build.gradle.kts")
    write_keystore_properties(built, app_dir)
    with open(build_file, "r", encoding="utf-8") as f:
        src = f.read()

    start = src.find(_MARKER_START)
    end = src.find(_MARKER_END)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError(
            f"Can't find flavor markers in {build_file}. Expected lines:\n"
            f"    {_MARKER_START}\n    {_MARKER_END}"
        )

    lines: list[str] = [
        _MARKER_START + " — managed by apps/generator/generate.py",
        "    signingConfigs {",
    ]
    for b in built:
        c = b.company
        ks = b.keystore
        rel_ks = os.path.relpath(ks.path, app_dir).replace("\\", "/")
        # The passwords are deliberately NOT written here. build.gradle.kts is
        # tracked, and a password committed once stays in the history even
        # after it is removed. They go to keystore.properties, which git
        # ignores, and signingSecret() reads them back at configure time.
        lines += [
            f'        create("{c.flavor}") {{',
            f'            storeFile = file("{rel_ks}")',
            f'            storePassword = signingSecret("{c.flavor}", "storePassword")',
            f'            keyAlias = "{_gradle_escape(ks.key_alias)}"',
            f'            keyPassword = signingSecret("{c.flavor}", "keyPassword")',
            '        }',
        ]
    lines += ["    }", "", "    productFlavors {"]
    for b in built:
        c = b.company
        arch   = archetype_for(c)
        vocab  = _sic_role_vocab(c)
        sic    = _sic_map.lookup(c.sic_codes)
        ver    = _load_version(c.company_number, c.company_name)
        import json as _json
        info_json = _json.dumps(sic["info_items"], ensure_ascii=True, separators=(",", ":"))
        lines += [
            f'        create("{c.flavor}") {{',
            '            dimension = "brand"',
            f'            applicationId = "{c.application_id}"',
            f'            versionCode = {ver["version_code"]}',
            f'            versionName = "{_gradle_escape(ver["version_name"])}"',
            f'            buildConfigField("String", "COMPANY_NAME", "\\"{_gradle_escape(c.display_name)}\\"")',
            f'            buildConfigField("String", "COMPANY_NUMBER", "\\"{_gradle_escape(c.company_number)}\\"")',
            f'            buildConfigField("String", "SUPPORT_EMAIL", "\\"{_gradle_escape(c.support_email)}\\"")',
            f'            buildConfigField("String", "COMPANY_DOMAIN", "\\"{_gradle_escape(c.domain)}\\"")',
            f'            buildConfigField("String", "PRIVACY_POLICY_URL", "\\"{_gradle_escape(_load_privacy_url(c.company_number))}\\"")',
            f'            buildConfigField("String", "CONTACT_ADDRESS", "\\"{_gradle_escape(c.address)}\\"")',
            f'            buildConfigField("String", "ROLE_NOUN", "\\"{vocab["ROLE_NOUN"]}\\"")',
            f'            buildConfigField("String", "ROLE_VERB_START", "\\"{vocab["ROLE_VERB_START"]}\\"")',
            f'            buildConfigField("String", "ROLE_VERB_END", "\\"{vocab["ROLE_VERB_END"]}\\"")',
            f'            buildConfigField("String", "EXPORT_TITLE", "\\"{vocab["EXPORT_TITLE"]}\\"")',
            f'            buildConfigField("String", "CALC_TITLE", "\\"{_gradle_escape(sic["calc_title"])}\\"")',
            f'            buildConfigField("String", "CALC_LABEL_A", "\\"{_gradle_escape(sic["calc_label_a"])}\\"")',
            f'            buildConfigField("String", "CALC_LABEL_B", "\\"{_gradle_escape(sic["calc_label_b"])}\\"")',
            f'            buildConfigField("String", "CALC_FORMULA", "\\"{sic["calc_formula"]}\\"")',
            f'            buildConfigField("String", "CALC_RESULT_LABEL", "\\"{_gradle_escape(sic["calc_result_label"])}\\"")',
            f'            buildConfigField("String", "INFO_TITLE", "\\"{_gradle_escape(sic["info_title"])}\\"")',
            f'            buildConfigField("String", "INFO_ITEMS_JSON", {_bc_str(info_json)})',
            f'            buildConfigField("String", "ACTION_LABEL", "\\"{_gradle_escape(sic["action_label"])}\\"")',
            f'            buildConfigField("String", "ARCHETYPE_ID", "\\"{arch.id}\\"")',
        ]
        for field_type, name, value in archetypes.build_config_fields(arch, c.sic_codes):
            if field_type == "String":
                literal = f'"\\"{_gradle_escape(str(value))}\\""'
            else:
                literal = f'"{value}"'
            lines.append(f'            buildConfigField("{field_type}", "{name}", {literal})')
        lines += [
            f'            signingConfig = signingConfigs.getByName("{c.flavor}")',
            '        }',
        ]
    lines += ["    }", ""]

    # Per-flavor archetype sources. A second flavor dimension would make Gradle
    # configure every company x every archetype; adding a source directory to
    # the flavor's own source set gives each app exactly one archetype with no
    # variant explosion. Exactly one activeArchetype() is on the compile path,
    # so the others contribute nothing to the artifact.
    lines.append("    sourceSets {")
    for b in built:
        arch = archetype_for(b.company)
        lines += [
            f'        getByName("{b.company.flavor}") {{',
            f'            java.srcDir("{arch.src_dir}/java")',
            '        }',
        ]
    lines += ["    }", "    " + _MARKER_END]
    block = "\n".join(lines)

    # Replace the whole line range [start-of-marker-line, end-of-marker-line]
    line_start = src.rfind("\n", 0, start) + 1   # start of the start-marker line
    line_end = src.find("\n", end)               # end of the end-marker line
    if line_end < 0:
        line_end = len(src)
    new_src = src[:line_start] + block + src[line_end:]
    with open(build_file, "w", encoding="utf-8") as f:
        f.write(new_src)
    return build_file


def clean_stale_flavors(app_dir: str, keep_flavors: set[str]) -> list[str]:
    """Remove src/c* dirs whose flavors aren't in keep_flavors. Returns removed paths."""
    src = os.path.join(app_dir, "src")
    if not os.path.isdir(src):
        return []
    removed = []
    for entry in os.listdir(src):
        if not entry.startswith("c"):
            continue
        if entry in {"main"}:
            continue
        if entry in keep_flavors:
            continue
        full = os.path.join(src, entry)
        if os.path.isdir(full):
            shutil.rmtree(full)
            removed.append(full)
    return removed
