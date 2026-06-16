"""Role vocabulary derived from company SIC codes."""

from __future__ import annotations

from .catalog import Company


def sic_role_vocab(company: Company) -> dict[str, str]:
    primary = company.sic_codes.split(",")[0].strip() if company.sic_codes else ""
    if primary in {"53202", "49410", "52100", "53100"}:
        return {
            "ROLE_NOUN": "trip",
            "ROLE_VERB_START": "Start Trip",
            "ROLE_VERB_END": "End Trip",
            "EXPORT_TITLE": "Delivery Log",
        }
    if primary in {"98000", "68320", "68100"}:
        return {
            "ROLE_NOUN": "visit",
            "ROLE_VERB_START": "Start Visit",
            "ROLE_VERB_END": "End Visit",
            "EXPORT_TITLE": "Site Visit Log",
        }
    if primary in {"87100", "88100", "86900"}:
        return {
            "ROLE_NOUN": "visit",
            "ROLE_VERB_START": "Start Visit",
            "ROLE_VERB_END": "End Visit",
            "EXPORT_TITLE": "Care Visit Log",
        }
    return {
        "ROLE_NOUN": "shift",
        "ROLE_VERB_START": "Start Shift",
        "ROLE_VERB_END": "End Shift",
        "EXPORT_TITLE": "Shift Log",
    }
