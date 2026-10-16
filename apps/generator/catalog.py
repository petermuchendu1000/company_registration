"""Load the company catalog from the tenant-scoped datastore."""
from __future__ import annotations
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _db():
    """
    Import the top-level db module.

    This package is imported both as `apps.generator.*` from the repo root and
    via a sys.path insert from pipeline.py, so the root is put on the path here
    rather than assumed.
    """
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    import db
    return db

# SIC code -> archetype. The table lives in archetypes.py, which is also what
# the Gradle source-set wiring and the policy gate read; re-exported here
# because callers have imported these two names from catalog since before the
# registry existed.
from .archetypes import SIC_ARCHETYPE, DEFAULT_ID as DEFAULT_ARCHETYPE  # noqa: E402
from . import archetypes as _archetypes  # noqa: E402


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


def load_companies(
    tenant_id: int | None = None,
    limit: int | None = None,
    company_numbers: list[str] | None = None,
) -> list[Company]:
    """
    Load one tenant's companies. Skips rows without a company number.

    tenant_id defaults to whatever is bound to the current request or job.
    db.current_tenant_id() raises when nothing is bound, so a caller that
    forgets gets an error instead of another client's companies.
    """
    db = _db()
    tid = db.current_tenant_id() if tenant_id is None else tenant_id
    rows = db.all_rows(tid)

    def field(row: dict, label: str) -> str:
        value = row.get(label)
        return str(value).strip() if value is not None else ""

    # Normalise filter set once
    filter_set = {n.zfill(8) if n.isdigit() and len(n) < 8 else n for n in (company_numbers or [])}

    out: list[Company] = []
    for row in rows:
        cn = field(row, "Company Number")
        if not cn:
            continue

        # Pad to 8 digits (Companies House format)
        if cn.isdigit() and len(cn) < 8:
            cn = cn.zfill(8)

        if filter_set and cn not in filter_set:
            continue

        sic_raw = field(row, "SIC Codes")
        archetype = _archetypes.for_sic(sic_raw).id

        domain = field(row, "Domain")
        email = field(row, "Assigned Email")
        support = f"dev@{domain}" if domain else (email or "support@example.uk")

        out.append(Company(
            company_number=cn,
            company_name=field(row, "Company Name"),
            sic_codes=sic_raw,
            domain=domain,
            support_email=support,
            archetype=archetype,
            short_name=field(row, "Short Name"),
            address=field(row, "Address"),
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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    cs = load_companies(limit=n)
    print(summarize(cs))
