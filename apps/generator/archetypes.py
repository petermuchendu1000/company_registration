"""Archetype registry — the one place that decides what an app *does*.

An archetype is a distinct feature set: its own data model, its own screens,
its own arithmetic and its own export. It is not a colour scheme and it is not
a word list. Google Play's Spam policy lists "creating multiple apps with
highly similar functionality, content, and user experience" as a violation of
Repetitive Content, so two companies in different trades must get apps that
differ in what they do, not only in what they are called.

Mechanically an archetype is a directory of Kotlin sources under
`apps/template-shift-journal/app/src/`. The generator adds exactly one of them
to each flavor's source set, so each built app compiles exactly one
implementation of `uk.company.utility.arch.activeArchetype()`. There is no
runtime switch, no reflection and no dead code in the shipped artifact.

Everything downstream reads this module: the SIC -> archetype mapping, the
Gradle source-set wiring, the per-flavor BuildConfig fields, the store listing
copy, and the policy gate's diversity check. Add an archetype here and in the
matching source directory, and nothing else needs to know.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Archetype:
    """One coherent app product.

    src_dir  — path relative to the Gradle `app/` directory. The generator
               appends `{src_dir}/java` to the flavor's Kotlin source set.
    modules  — the screens this archetype contributes, named for the listing.
               The policy gate reads these to tell archetypes apart.

    screenshot_screens is the listing's caption for each store screenshot,
    in order. The pictures themselves are rendered from the built app by
    ListingScreenshotTest, not from anything here; the policy gate checks
    that this list and what the test produced are the same length and for
    the same archetype, so a caption cannot describe a screen that is not
    in the set.
    """

    id: str
    src_dir: str
    label: str
    summary: str
    modules: tuple[str, ...]
    screenshot_screens: tuple[str, ...]


SHIFT = Archetype(
    id="shift",
    src_dir="src/archetype-shift",
    label="Shift journal",
    summary=(
        "A running timer for attendance work: start a session, add a note, end "
        "it, and keep a local history with daily counts."
    ),
    modules=("timer_home", "session_log"),
    screenshot_screens=(
        "Home — timer and recent activity",
        "Log — session history with per-day totals",
        "Reference — trade figures",
        "Contact — address, email and website",
    ),
)

MILEAGE = Archetype(
    id="mileage",
    src_dir="src/archetype-mileage",
    label="Mileage log",
    summary=(
        "An odometer-based journey log for drivers: record start and end "
        "readings, and see HMRC approved-mileage relief accrue across the tax "
        "year with the 10,000-mile band applied in order."
    ),
    modules=("journey_entry", "journey_list", "tax_year_summary"),
    screenshot_screens=(
        "Home — journey entry and tax-year relief",
        "Journeys — journey history grouped by month",
        "Reference — trade figures",
        "Contact — address, email and website",
    ),
)

QUOTE = Archetype(
    id="quote",
    src_dir="src/archetype-quote",
    label="Quote builder",
    summary=(
        "A priced estimate builder for trades: add labour and materials as "
        "line items, see net, VAT and total update as you go, save the quote "
        "against a client and send it as text."
    ),
    modules=("quote_builder", "line_items", "saved_quotes"),
    screenshot_screens=(
        "Estimate — lines, VAT and total",
        "Saved — estimates by client with totals",
        "Reference — trade figures",
        "Contact — address, email and website",
    ),
)

ALL: tuple[Archetype, ...] = (SHIFT, MILEAGE, QUOTE)
BY_ID: dict[str, Archetype] = {a.id: a for a in ALL}
DEFAULT_ID = SHIFT.id


# ── SIC -> archetype ────────────────────────────────────────────────────────
# Only trades whose daily record is a distance get the mileage app. Everything
# else records a span of time, which is the shift app. A trade with neither is
# a signal that a third archetype is needed, not a reason to stretch one of
# these two.
SIC_ARCHETYPE: dict[str, str] = {
    # Distance-based: couriers, hauliers, taxis, removals, postal.
    "49320": MILEAGE.id,   # Taxi operation
    "49390": MILEAGE.id,   # Other passenger land transport
    "49410": MILEAGE.id,   # Freight transport by road
    "49420": MILEAGE.id,   # Removal services
    "53100": MILEAGE.id,   # Postal activities under universal service obligation
    "53201": MILEAGE.id,   # Licensed carriers
    "53202": MILEAGE.id,   # Unlicensed carriers (courier)
    # Time-based: staffing, care, property, storage.
    "52100": SHIFT.id,     # Warehousing and storage
    "78200": SHIFT.id,     # Temporary employment agency
    "87100": SHIFT.id,     # Residential nursing care
    "88100": SHIFT.id,     # Social work without accommodation
    "98000": SHIFT.id,     # Residents property management
    # Job-priced: construction and the building trades, where the daily
    # document is an estimate rather than a record of time or distance.
    "41100": QUOTE.id,     # Development of building projects
    "41201": QUOTE.id,     # Construction of commercial buildings
    "41202": QUOTE.id,     # Construction of domestic buildings
    "43210": QUOTE.id,     # Electrical installation
    "43220": QUOTE.id,     # Plumbing, heat and air-conditioning
    "43320": QUOTE.id,     # Joinery installation
    "43341": QUOTE.id,     # Painting
    "43910": QUOTE.id,     # Roofing
    "43999": QUOTE.id,     # Other specialised construction
    "81210": QUOTE.id,     # General cleaning of buildings
    "81300": QUOTE.id,     # Landscape service activities
}

_PREFIX_ARCHETYPE: dict[str, str] = {
    "41": QUOTE.id,
    "43": QUOTE.id,
    "49": MILEAGE.id,
    "53": MILEAGE.id,
}


def for_sic(sic_codes: str) -> Archetype:
    """Pick an archetype from a comma-separated SIC string. Exact, then 2-digit."""
    primary = (sic_codes.split(",")[0].strip() if sic_codes else "")
    if primary in SIC_ARCHETYPE:
        return BY_ID[SIC_ARCHETYPE[primary]]
    if primary[:2] in _PREFIX_ARCHETYPE:
        return BY_ID[_PREFIX_ARCHETYPE[primary[:2]]]
    return BY_ID[DEFAULT_ID]


def get(archetype_id: str) -> Archetype:
    """Resolve an id, falling back to the default rather than raising.

    A company row can carry an archetype id written by an older build. Falling
    back keeps that company buildable; the policy gate reports the mismatch.
    """
    return BY_ID.get(archetype_id, BY_ID[DEFAULT_ID])


# ── vocabulary ──────────────────────────────────────────────────────────────
# Within an archetype the wording still follows the trade: a courier logs a
# "delivery run", a haulier a "trip". This is presentation only — the screens
# and the arithmetic come from the archetype above.

_SHIFT_VOCAB: list[tuple[frozenset[str], dict[str, str]]] = [
    (frozenset({"98000", "68320", "68100"}), {
        "ROLE_NOUN": "visit",
        "ROLE_VERB_START": "Start Visit",
        "ROLE_VERB_END": "End Visit",
        "EXPORT_TITLE": "Site Visit Log",
    }),
    (frozenset({"87100", "88100", "86900"}), {
        "ROLE_NOUN": "visit",
        "ROLE_VERB_START": "Start Visit",
        "ROLE_VERB_END": "End Visit",
        "EXPORT_TITLE": "Care Visit Log",
    }),
    (frozenset({"52100"}), {
        "ROLE_NOUN": "shift",
        "ROLE_VERB_START": "Start Shift",
        "ROLE_VERB_END": "End Shift",
        "EXPORT_TITLE": "Depot Shift Log",
    }),
]

_SHIFT_DEFAULT_VOCAB = {
    "ROLE_NOUN": "shift",
    "ROLE_VERB_START": "Start Shift",
    "ROLE_VERB_END": "End Shift",
    "EXPORT_TITLE": "Shift Log",
}

_MILEAGE_VOCAB: list[tuple[frozenset[str], dict[str, str]]] = [
    (frozenset({"53202", "53201", "53100"}), {
        "ROLE_NOUN": "delivery run",
        "ROLE_VERB_START": "Log Run",
        "ROLE_VERB_END": "Save Run",
        "EXPORT_TITLE": "Delivery Mileage",
    }),
    (frozenset({"49320", "49390"}), {
        "ROLE_NOUN": "fare",
        "ROLE_VERB_START": "Log Fare",
        "ROLE_VERB_END": "Save Fare",
        "EXPORT_TITLE": "Fare Mileage",
    }),
]

_MILEAGE_DEFAULT_VOCAB = {
    "ROLE_NOUN": "journey",
    "ROLE_VERB_START": "Log Journey",
    "ROLE_VERB_END": "Save Journey",
    "EXPORT_TITLE": "Mileage Log",
}

_QUOTE_VOCAB: list[tuple[frozenset[str], dict[str, str]]] = [
    (frozenset({"41100", "41201", "41202"}), {
        "ROLE_NOUN": "tender",
        "ROLE_VERB_START": "New Tender",
        "ROLE_VERB_END": "Save Tender",
        "EXPORT_TITLE": "Tenders",
    }),
    (frozenset({"81210", "81300"}), {
        "ROLE_NOUN": "quotation",
        "ROLE_VERB_START": "New Quotation",
        "ROLE_VERB_END": "Save Quotation",
        "EXPORT_TITLE": "Quotations",
    }),
]

_QUOTE_DEFAULT_VOCAB = {
    "ROLE_NOUN": "estimate",
    "ROLE_VERB_START": "New Estimate",
    "ROLE_VERB_END": "Save Estimate",
    "EXPORT_TITLE": "Estimates",
}

_VOCAB_BY_ARCHETYPE: dict[str, tuple[list, dict]] = {
    SHIFT.id: (_SHIFT_VOCAB, _SHIFT_DEFAULT_VOCAB),
    MILEAGE.id: (_MILEAGE_VOCAB, _MILEAGE_DEFAULT_VOCAB),
    QUOTE.id: (_QUOTE_VOCAB, _QUOTE_DEFAULT_VOCAB),
}


def vocab_for(archetype: Archetype, sic_codes: str) -> dict[str, str]:
    """Role wording for a company, within its archetype."""
    primary = (sic_codes.split(",")[0].strip() if sic_codes else "")
    table, default = _VOCAB_BY_ARCHETYPE.get(
        archetype.id, (_SHIFT_VOCAB, _SHIFT_DEFAULT_VOCAB),
    )
    for codes, words in table:
        if primary in codes:
            return dict(words)
    return dict(default)


# ── BuildConfig ─────────────────────────────────────────────────────────────
# Fields only the archetype's own Kotlin reads. They are emitted for the
# flavors using that archetype and nowhere else, so an unused constant can
# never drift out of step with the code that would have read it.
#
# Each entry is (gradle_type, field_name, python_value). The writer formats the
# literal; ints go through unquoted, strings get escaped.

#: HMRC Approved Mileage Allowance Payments, cars and vans, from 2011-12.
#: https://www.gov.uk/guidance/travel-mileage-and-fuel-rates-and-allowances
AMAP_FIRST_10K_PENCE = 45
AMAP_AFTER_10K_PENCE = 25
AMAP_BAND_MILES = 10_000


#: Standard UK VAT. Used by the quote archetype's totals.
VAT_PERCENT = 20


def build_config_fields(archetype: Archetype, sic_codes: str) -> list[tuple[str, str, object]]:
    primary = (sic_codes.split(",")[0].strip() if sic_codes else "")
    if archetype.id == MILEAGE.id:
        vehicle = "Van" if primary in {"49410", "49420"} else "Vehicle"
        return [
            ("int", "AMAP_FIRST_RATE_PENCE", AMAP_FIRST_10K_PENCE),
            ("int", "AMAP_AFTER_RATE_PENCE", AMAP_AFTER_10K_PENCE),
            ("int", "AMAP_BAND_MILES", AMAP_BAND_MILES),
            ("String", "VEHICLE_LABEL", vehicle),
        ]
    if archetype.id == QUOTE.id:
        # What the first line of a new quote is usually for, by trade.
        unit = {
            "43210": "Circuit",
            "43220": "Fitting",
            "43320": "Unit",
            "43341": "Room",
            "43910": "m²",
            "81210": "Visit",
            "81300": "Visit",
        }.get(primary, "Item")
        return [
            ("int", "VAT_PERCENT", VAT_PERCENT),
            ("String", "LINE_UNIT_LABEL", unit),
        ]
    return []
