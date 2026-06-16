"""Fast generator quality checks that do not require the Android SDK."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .catalog import load_companies
from .functionality_catalog import BASE_MODULES, selected_modules
from .listing import full_description, short_description
from .role_vocab import sic_role_vocab

ROOT = Path(__file__).resolve().parents[2]
MAIN_ACTIVITY = ROOT / "apps" / "template-shift-journal" / "app" / "src" / "main" / "java" / "uk" / "template" / "shift" / "MainActivity.kt"
TYPOGRAPHY = ROOT / "apps" / "template-shift-journal" / "app" / "src" / "main" / "java" / "uk" / "template" / "shift" / "ui" / "theme" / "Typography.kt"


def _failures_for_company(company, minimum_modules: int) -> list[str]:
    failures: list[str] = []
    modules = selected_modules(company, minimum=minimum_modules)
    vocab = sic_role_vocab(company)
    short = short_description(vocab["ROLE_NOUN"], [module.__dict__ for module in modules])
    full = full_description(
        company.display_name,
        vocab["ROLE_NOUN"],
        vocab["EXPORT_TITLE"],
        [module.__dict__ for module in modules],
    )

    if len(company.display_name) > 30:
        failures.append(f"{company.company_number}: display name exceeds 30 chars: {company.display_name}")
    if len(modules) < minimum_modules:
        failures.append(f"{company.company_number}: selected only {len(modules)} modules")
    if len({module.key for module in modules}) != len(modules):
        failures.append(f"{company.company_number}: duplicate selected module keys")
    if not (20 <= len(short) <= 80):
        failures.append(f"{company.company_number}: short description length is {len(short)}")
    if not (500 <= len(full) <= 4000):
        failures.append(f"{company.company_number}: full description length is {len(full)}")
    if "PENDING_" in short or "PENDING_" in full:
        failures.append(f"{company.company_number}: listing text contains placeholder")
    return failures


def _template_failures() -> list[str]:
    failures: list[str] = []
    main = MAIN_ACTIVITY.read_text(encoding="utf-8") if MAIN_ACTIVITY.exists() else ""
    typography = TYPOGRAPHY.read_text(encoding="utf-8") if TYPOGRAPHY.exists() else ""

    required_tokens = [
        "MODULE_KEYS",
        "ModuleTabs",
        "OutlinedTextField",
        "Recent activity",
        "PrivacyPanel",
        "rememberSaveable",
    ]
    for token in required_tokens:
        if token not in main:
            failures.append(f"template: MainActivity.kt missing {token}")

    non_zero_spacing = re.findall(r"letterSpacing\s*=\s*(?!0\.sp)([0-9.]+\.sp)", typography)
    if non_zero_spacing:
        failures.append(f"template: non-zero letterSpacing values found: {', '.join(non_zero_spacing)}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run non-SDK generator quality checks")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--minimum-modules", type=int, default=5)
    args = parser.parse_args(argv)

    failures: list[str] = []
    if len(BASE_MODULES) < 10:
        failures.append(f"catalog: expected at least 10 modules, found {len(BASE_MODULES)}")
    if len({module.key for module in BASE_MODULES}) != len(BASE_MODULES):
        failures.append("catalog: duplicate module keys")

    companies = load_companies(limit=None)[args.offset:args.offset + args.count]
    if not companies:
        failures.append("catalog: no companies loaded")
    for company in companies:
        failures.extend(_failures_for_company(company, args.minimum_modules))
    failures.extend(_template_failures())

    if failures:
        print(f"Self-check failed: {len(failures)} issue(s)")
        for failure in failures:
            print(f"FAIL {failure}")
        return 1

    print(f"Self-check passed for {len(companies)} companies")
    print(f"Catalog modules: {len(BASE_MODULES)}")
    print(f"Minimum selected modules per app: {args.minimum_modules}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
