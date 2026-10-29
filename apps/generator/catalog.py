"""Load the company catalog from the processed pipeline Excel."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from typing import Iterable
import openpyxl

EXCEL_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pipeline_output",
    "companies_pipeline.xlsx",
)

# SIC code -> archetype. Only "shift" exists today; the rest are placeholders
# for future archetype modules and currently fall back to "shift".
SIC_ARCHETYPE = {
    "78200": "shift",  # Temp employment agency
    "88100": "shift",  # Social work without accommodation for the elderly/disabled
    "87100": "shift",  # Residential nursing care
    "53202": "shift",  # Courier (will become "mileage" when that archetype lands)
    "49410": "shift",  # Freight road transport
    "98000": "shift",  # Residents property mgmt (will become "twa" when that lands)
}
DEFAULT_ARCHETYPE = "shift"


@dataclass(frozen=True)
class Company:
    company_number: str
    company_name: str
    sic_codes: str
    domain: str
    support_email: str
    archetype: str
    short_name: str = ""
    address: str = ""

    @property
    def flavor(self) -> str:
        return f"c{self.company_number}"

    @property
    def slug(self) -> str:
        """Unique lowercase slug from company name — used as applicationId segment."""
        name = re.sub(r"\s+(LTD|LIMITED|LLP|PLC|INC)\.?$", "", self.company_name, flags=re.IGNORECASE)
        for stop in ("management", "services", "solutions", "group", "holdings",
                     "enterprises", "consulting", "associates", "the", "and", "of"):
            name = re.sub(rf"\b{stop}\b", "", name, flags=re.IGNORECASE)
        slug = re.sub(r"[^a-zA-Z0-9]", "", name).lower()
        if not slug or not slug[0].isalpha():
            slug = "co" + slug
        return slug[:20] or f"co{self.company_number}"

    @property
    def application_id(self) -> str:
        return f"uk.{self.slug}.app"

    @property
    def display_name(self) -> str:
        """Launcher label — prefers curated short_name; falls back to
        title-cased company name with LTD/LIMITED stripped, 30-char cap
        that breaks on a word boundary to avoid mid-word cut-offs."""
        if self.short_name:
            return self.short_name[:30].rstrip()

        name = self.company_name.strip()
        name = re.sub(r"\s+(LTD|LIMITED|LLP|PLC)\.?$", "", name, flags=re.IGNORECASE)
        name = name.title()
        if len(name) <= 30:
            return name
        # Truncate at last space within the 30-char window
        truncated = name[:30]
        last_space = truncated.rfind(" ")
        return (truncated[:last_space] if last_space > 0 else truncated).rstrip()


def _rows_from_db() -> list[dict]:
    """Company rows from Supabase, keyed by the header labels db.COLUMNS uses.

    db is imported here rather than at module scope because this file lives
    in apps/generator and the repository root is only on sys.path once a
    caller has put it there (pipeline.py:750 does). A module-level import
    would make the package unimportable from anywhere else.
    """
    import sys
    from pathlib import Path as _P
    root = str(_P(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    import db
    return db.all_rows()


def _rows_from_excel(excel_path: str) -> list[dict]:
    """The same shape, read from the pipeline workbook."""
    wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
    try:
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [(h.strip() if isinstance(h, str) else h) for h in next(it)]
        return [{h: v for h, v in zip(header, row) if h} for row in it]
    finally:
        wb.close()


def load_companies(
    excel_path: str | None = None,
    limit: int | None = None,
    company_numbers: list[str] | None = None,
) -> list[Company]:
    """Companies for the app build. Skips rows without a company number.

    Reads the DATABASE by default. It used to read
    pipeline_output/companies_pipeline.xlsx, which nothing writes from the
    database - db.py's CLI has only --diff and --import, both Excel to
    database, and no export - so the app build and the rest of the pipeline
    were reading two different stores. On a server where the sheet had
    never been produced, stage 5 raised FileNotFoundError; worse, where an
    old sheet DID exist it would have built a stale set of companies while
    every other stage used the database.

    Passing excel_path still reads a workbook, so the standalone tools that
    work from a sheet keep working. None of the three callers passes it.
    """
    rows = _rows_from_excel(excel_path) if excel_path else _rows_from_db()

    def get(row: dict, name: str) -> str:
        v = row.get(name)
        return str(v).strip() if v is not None else ""

    # Normalise filter set once
    filter_set = {n.zfill(8) if n.isdigit() and len(n) < 8 else n for n in (company_numbers or [])}

    out: list[Company] = []
    for row in rows:
        cn = get(row, "Company Number")
        if not cn:
            continue
        # Pad to 8 digits (Companies House format)
        if cn.isdigit() and len(cn) < 8:
            cn = cn.zfill(8)

        if filter_set and cn not in filter_set:
            continue

        sic_raw = get(row, "SIC Codes")
        primary_sic = sic_raw.split(",")[0].strip() if sic_raw else ""
        archetype = SIC_ARCHETYPE.get(primary_sic, DEFAULT_ARCHETYPE)

        domain = get(row, "Domain")
        email  = get(row, "Assigned Email")
        # The support address is the dev@ forward stage 3 creates on the
        # company's own domain, so it carries the company domain by
        # construction rather than by being typed in.
        support = f"dev@{domain}" if domain else (email or "support@example.uk")

        out.append(Company(
            company_number=cn,
            company_name=get(row, "Company Name"),
            sic_codes=sic_raw,
            domain=domain,
            support_email=support,
            archetype=archetype,
            short_name=get(row, "Short Name"),
            address=get(row, "Address"),
        ))

        if limit is not None and len(out) >= limit:
            break

    return out


def summarize(companies: Iterable[Company]) -> str:
    lines = [f"{'#':>3} {'Flavor':<12} {'SIC':<6} {'Arch':<6} Name"]
    for i, c in enumerate(companies, 1):
        primary_sic = c.sic_codes.split(",")[0].strip() if c.sic_codes else "-"
        lines.append(f"{i:>3} {c.flavor:<12} {primary_sic:<6} {c.archetype:<6} {c.display_name}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    cs = load_companies(limit=n)
    print(summarize(cs))
