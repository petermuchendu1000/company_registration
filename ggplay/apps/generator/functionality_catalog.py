"""Mutation-safe local app modules.

Every module is intentionally offline, zero-permission, and suitable for a
privacy-first business utility. The generator selects a deterministic subset per
company so apps are not just color/name variants.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .catalog import Company
from .role_vocab import sic_role_vocab


@dataclass(frozen=True)
class Module:
    key: str
    title: str
    nav_label: str
    short_description: str
    detail: str
    primary_action: str
    secondary_action: str
    metric_label: str
    sample_value: str


BASE_MODULES: list[Module] = [
    Module(
        "work_log",
        "Work Log",
        "Log",
        "Capture local session entries with a clear start and finish flow.",
        "Record each work session as a simple local entry with status, count, and notes.",
        "Start Entry",
        "Close Entry",
        "Entries",
        "3",
    ),
    Module(
        "checklist",
        "Checklist",
        "Tasks",
        "Track routine checks without needing an account or network access.",
        "Use a compact checklist for pre-work, handover, site, or delivery checks.",
        "Mark Done",
        "Reset List",
        "Done",
        "4/6",
    ),
    Module(
        "daily_plan",
        "Daily Plan",
        "Plan",
        "Outline the next few local tasks for the day.",
        "Keep a short work plan visible so repeated actions stay organized.",
        "Add Plan",
        "Review Plan",
        "Planned",
        "5",
    ),
    Module(
        "notes",
        "Notes",
        "Notes",
        "Write local notes for handover, visit context, or follow-up reminders.",
        "Notes are stored only on the device and can be cleared with app storage.",
        "Save Note",
        "Clear Draft",
        "Drafts",
        "1",
    ),
    Module(
        "history",
        "History",
        "History",
        "Review recent activity in a calm chronological view.",
        "Recent entries make it easier to confirm what happened during the session.",
        "Review",
        "Pin Item",
        "Recent",
        "8",
    ),
    Module(
        "insights",
        "Insights",
        "Insights",
        "Summarize local counts and completion status.",
        "A small dashboard highlights today's count, open items, and completion ratio.",
        "Refresh",
        "Compare",
        "Score",
        "82%",
    ),
    Module(
        "handover",
        "Handover",
        "Handover",
        "Prepare a concise end-of-session handover note.",
        "Capture what is complete, what remains open, and what needs attention next.",
        "Prepare",
        "Complete",
        "Open",
        "2",
    ),
    Module(
        "inventory",
        "Inventory Check",
        "Stock",
        "Track simple item checks for equipment, supplies, or delivery materials.",
        "A lightweight local stock checklist helps avoid missed equipment or supply checks.",
        "Check Item",
        "Flag Item",
        "Checked",
        "7",
    ),
    Module(
        "mileage",
        "Mileage Notes",
        "Mileage",
        "Keep simple trip or route notes without location permissions.",
        "Mileage notes are typed manually; the app never reads GPS or background location.",
        "Add Trip",
        "Close Trip",
        "Trips",
        "2",
    ),
    Module(
        "incident",
        "Incident Notes",
        "Incident",
        "Capture a private local note when something needs follow-up.",
        "Incident notes are plain local text for internal memory, not reporting automation.",
        "Add Note",
        "Mark Stable",
        "Open",
        "0",
    ),
    Module(
        "reference",
        "Reference",
        "Reference",
        "Keep a short local reference panel for repeated work reminders.",
        "Reference cards help users remember routine steps without leaving the app.",
        "Open Card",
        "Review",
        "Cards",
        "6",
    ),
    Module(
        "contacts",
        "Support Info",
        "Support",
        "Show company support details and privacy information clearly.",
        "Support details are displayed as text only; the app does not access contacts.",
        "View Support",
        "Privacy",
        "Items",
        "3",
    ),
]


SIC_PREFERRED_MODULES = {
    "53202": ["mileage", "checklist", "history", "daily_plan", "incident"],
    "49410": ["mileage", "inventory", "checklist", "history", "handover"],
    "98000": ["checklist", "incident", "reference", "history", "handover"],
    "68320": ["checklist", "incident", "reference", "history", "handover"],
    "87100": ["handover", "notes", "checklist", "history", "incident"],
    "88100": ["handover", "notes", "checklist", "history", "incident"],
    "86900": ["handover", "notes", "checklist", "history", "incident"],
    "78200": ["work_log", "daily_plan", "handover", "notes", "insights"],
}


def _module_by_key() -> dict[str, Module]:
    return {module.key: module for module in BASE_MODULES}


def selected_modules(company: Company, minimum: int = 5) -> list[Module]:
    primary_sic = company.sic_codes.split(",")[0].strip() if company.sic_codes else ""
    by_key = _module_by_key()
    keys: list[str] = []

    for key in SIC_PREFERRED_MODULES.get(primary_sic, []):
        if key not in keys:
            keys.append(key)

    digest = hashlib.sha256(f"{company.company_number}:{company.company_name}".encode("utf-8")).digest()
    ranked = sorted(
        BASE_MODULES,
        key=lambda module: hashlib.sha256(digest + module.key.encode("utf-8")).hexdigest(),
    )
    for module in ranked:
        if module.key not in keys:
            keys.append(module.key)
        if len(keys) >= minimum:
            break

    return [by_key[key] for key in keys[:minimum]]


def module_payload(company: Company) -> dict:
    modules = selected_modules(company)
    vocab = sic_role_vocab(company)
    return {
        "role_noun": vocab["ROLE_NOUN"],
        "role_start": vocab["ROLE_VERB_START"],
        "role_end": vocab["ROLE_VERB_END"],
        "export_title": vocab["EXPORT_TITLE"],
        "modules": [
            {
                "key": module.key,
                "title": module.title,
                "nav_label": module.nav_label,
                "short_description": module.short_description,
                "detail": module.detail,
                "primary_action": module.primary_action,
                "secondary_action": module.secondary_action,
                "metric_label": module.metric_label,
                "sample_value": module.sample_value,
            }
            for module in modules
        ],
    }


def build_config_delimited(company: Company, field: str) -> str:
    payload = module_payload(company)
    return "|".join(str(module[field]).replace("|", "/") for module in payload["modules"])
