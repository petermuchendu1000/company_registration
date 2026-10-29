"""Emit Gradle flavors + per-flavor Android resource overlays.

Writes two things:
  1. {app}/build.gradle.kts            — rendered from build.gradle.kts.template,
                                         one productFlavor + signingConfig per
                                         company spliced between the markers
  2. {app}/src/c{cn}/res/values/*.xml  — per-flavor colors + strings

{app}/keystores/{cn}.jks comes from keystore_mint, which is the caller's
responsibility.

build.gradle.kts is GENERATED and git-ignored; build.gradle.kts.template is
the tracked source. This docstring used to describe a flavors.gradle.kts
applied via `apply(from=...)`, which no code here has ever written - the
markers sit INSIDE the android {} block, so the content could not be a
separate applied script without also wrapping itself in android {}.
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
from . import sic_map as _sic_map

_ROOT = Path(__file__).parent.parent.parent


def _load_version(company_number: str, company_name: str = "") -> dict:
    """Read version.json for this company. Returns defaults if not found."""
    import re as _re
    safe = _re.sub(r'[\\/:*?"<>|]', "", company_name).strip()
    folder = f"{company_number} - {safe}" if safe else company_number
    ver_file = _ROOT / "pipeline_output" / "companies" / folder / "app" / "version.json"
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
    import re as _re
    safe = _re.sub(r'[\\/:*?"<>|]', "", company_name).strip()
    folder = f"{company_number} - {safe}" if safe else company_number
    candidates = [
        _ROOT / "pipeline_output" / "companies" / folder / "app" / "manifest.json",
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


def _sic_role_vocab(company: Company) -> dict[str, str]:
    """Map primary SIC -> role-vocabulary strings baked into BuildConfig."""
    primary = (company.sic_codes.split(",")[0].strip() if company.sic_codes else "")
    if primary in {"53202", "49410", "52100", "53100"}:      # courier/logistics
        return {
            "ROLE_NOUN": "trip",
            "ROLE_VERB_START": "Start Trip",
            "ROLE_VERB_END": "End Trip",
            "EXPORT_TITLE": "Delivery Log",
        }
    if primary in {"98000", "68320", "68100"}:               # property mgmt
        return {
            "ROLE_NOUN": "visit",
            "ROLE_VERB_START": "Start Visit",
            "ROLE_VERB_END": "End Visit",
            "EXPORT_TITLE": "Site Visit Log",
        }
    if primary in {"87100", "88100", "86900"}:               # care
        return {
            "ROLE_NOUN": "visit",
            "ROLE_VERB_START": "Start Visit",
            "ROLE_VERB_END": "End Visit",
            "EXPORT_TITLE": "Care Visit Log",
        }
    # default: shift work
    return {
        "ROLE_NOUN": "shift",
        "ROLE_VERB_START": "Start Shift",
        "ROLE_VERB_END": "End Shift",
        "EXPORT_TITLE": "Shift Log",
    }


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


_TEMPLATE_NAME = "build.gradle.kts.template"
_GENERATED_NAME = "build.gradle.kts"

_GENERATED_HEADER = (
    "// GENERATED FILE - do not edit, and do not commit.\n"
    "//\n"
    f"// Written by apps/generator/flavor_writer.py from {_TEMPLATE_NAME}\n"
    "// on every build. Edit that template instead; anything written here\n"
    "// is replaced without warning the next time the generator runs.\n"
)


def write_flavors_gradle(built: list[BuiltFlavor], app_dir: str) -> str:
    """Render build.gradle.kts from its template, with the flavors spliced in.

    This used to read and rewrite build.gradle.kts ITSELF, and that file was
    git-tracked. Two consequences, both of which happened:

      - Every build left the repository dirty, so the next `git pull`
        conflicted against a file nobody had edited.
      - The block spliced in carries each flavor's keystore storePassword and
        keyPassword as plaintext literals. Writing them into a tracked file
        in a PUBLIC repository is how one such password came to sit in
        commit 8d0f76f. test_no_committed_secrets.py exists because of it.

    So the tracked file is now build.gradle.kts.template, which contains no
    generated content and no secret, and build.gradle.kts is generated beside
    it and git-ignored. Gradle is handed the same bytes as before.

    Reading the TEMPLATE rather than the output also makes the run
    idempotent in a way it was not: the old code parsed the file it had
    itself last written, so a previously generated file was the input to
    the next generation.

    Calling this is not optional before a build. The template's marker block
    is empty, so a project built from it has no product flavors and
    therefore no BuildConfig fields - measured: 66 unresolved references in
    MainActivity.kt, COMPANY_NAME and ROLE_NOUN among them. Every caller
    already does it: generate.py:241 before :256, generate.py:321 before
    :334, and pipeline.py:773 before :783.
    """
    template_file = os.path.join(app_dir, _TEMPLATE_NAME)
    build_file = os.path.join(app_dir, _GENERATED_NAME)
    if not os.path.exists(template_file):
        raise RuntimeError(
            f"{template_file} is missing. It is the tracked source for "
            f"{_GENERATED_NAME}, which is generated and git-ignored; a "
            f"checkout without it cannot build."
        )
    with open(template_file, "r", encoding="utf-8") as f:
        src = f.read()

    start = src.find(_MARKER_START)
    end = src.find(_MARKER_END)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError(
            f"Can't find flavor markers in {template_file}. Expected lines:\n"
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
        lines += [
            f'        create("{c.flavor}") {{',
            f'            storeFile = file("{rel_ks}")',
            f'            storePassword = "{_gradle_escape(ks.store_password)}"',
            f'            keyAlias = "{_gradle_escape(ks.key_alias)}"',
            f'            keyPassword = "{_gradle_escape(ks.key_password)}"',
            '        }',
        ]
    lines += ["    }", "", "    productFlavors {"]
    for b in built:
        c = b.company
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
            f'            signingConfig = signingConfigs.getByName("{c.flavor}")',
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
    # The header is a comment, so it is inert to Gradle: the Kotlin DSL
    # requires `plugins {}` to be the first STATEMENT, and a comment is not
    # one. Verified by compiling the generated project.
    with open(build_file, "w", encoding="utf-8") as f:
        f.write(_GENERATED_HEADER + new_src)
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
