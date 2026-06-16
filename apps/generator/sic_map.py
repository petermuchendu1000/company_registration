"""SIC code → per-company app content data.

Each entry drives:
  - Calculator screen  (calc_*)
  - Reference screen   (info_*)
  - Contact label      (action_label)

Lookup order: exact 5-digit → 2-digit prefix → "default".
"""
from __future__ import annotations
import re

# ── Types ─────────────────────────────────────────────────────────────────────

def _entry(
    calc_title: str,
    calc_label_a: str,
    calc_label_b: str,
    calc_formula: str,        # MULTIPLY | DIVIDE | PERCENT | VAT_ADD | STAMP_DUTY
    calc_result_label: str,
    info_title: str,
    info_items: list[dict],   # [{"k": "...", "v": "..."}, ...]
    action_label: str = "Get in Touch",
) -> dict:
    return {
        "calc_title": calc_title,
        "calc_label_a": calc_label_a,
        "calc_label_b": calc_label_b,
        "calc_formula": calc_formula,
        "calc_result_label": calc_result_label,
        "info_title": info_title,
        "info_items": info_items,
        "action_label": action_label,
    }


# ── Exact SIC entries ─────────────────────────────────────────────────────────

_EXACT: dict[str, dict] = {

    # ── Staffing / Recruitment ────────────────────────────────────────────────
    "78100": _entry(
        "Permanent Salary Calculator", "Annual Salary (£)", "Agency Fee (%)",
        "PERCENT", "Agency Fee",
        "Recruitment Reference",
        [
            {"k": "National Living Wage", "v": "£11.44 / hr (Apr 2024)"},
            {"k": "Employer NI Threshold", "v": "£9,100 / yr"},
            {"k": "Employer NI Rate", "v": "13.8% above threshold"},
            {"k": "Statutory Holiday", "v": "28 days (incl. 8 public)"},
            {"k": "Auto-Enrolment Min.", "v": "3% employer / 5% employee"},
            {"k": "Apprenticeship Levy", "v": "0.5% of payroll > £3m"},
            {"k": "Statutory Sick Pay", "v": "£116.75 / week (2024)"},
            {"k": "Redundancy Pay Cap", "v": "£643 / week (Apr 2024)"},
        ],
        "Request a Quote",
    ),
    "78200": _entry(
        "Day Rate Calculator", "Daily Rate (£)", "Number of Days",
        "MULTIPLY", "Total Earnings",
        "Employment Reference",
        [
            {"k": "National Living Wage", "v": "£11.44 / hr (Apr 2024)"},
            {"k": "Employer NI Threshold", "v": "£9,100 / yr"},
            {"k": "Employer NI Rate", "v": "13.8% above threshold"},
            {"k": "Statutory Holiday", "v": "28 days incl. 8 bank holidays"},
            {"k": "Holiday Pay Rate", "v": "12.07% of pay (part-time)"},
            {"k": "Auto-Enrolment Min.", "v": "3% employer contribution"},
            {"k": "IR35 Off-Payroll", "v": "Applies April 2021 (medium/large)"},
            {"k": "Statutory Sick Pay", "v": "£116.75 / week (2024)"},
        ],
        "Book a Worker",
    ),
    "78300": _entry(
        "Hourly Rate Calculator", "Hourly Rate (£)", "Hours Worked",
        "MULTIPLY", "Gross Pay",
        "HR Quick Reference",
        [
            {"k": "National Living Wage (25+)", "v": "£11.44 / hr"},
            {"k": "18–20 Minimum Wage", "v": "£8.60 / hr"},
            {"k": "Apprentice Minimum", "v": "£6.40 / hr"},
            {"k": "Overtime Threshold", "v": "Above contracted hours"},
            {"k": "Working Time Directive", "v": "Max 48 hrs / week (average)"},
            {"k": "Rest Break", "v": "20 min for 6+ hr shifts"},
            {"k": "Payslip Requirement", "v": "Itemised payslip by law"},
            {"k": "P60 Deadline", "v": "By 31 May each year"},
        ],
    ),

    # ── Property Management ───────────────────────────────────────────────────
    "68320": _entry(
        "Service Charge Calculator", "Annual Budget (£)", "Number of Units",
        "DIVIDE", "Charge Per Unit",
        "Property Management Reference",
        [
            {"k": "LTA 1985 Section 19", "v": "Charges must be reasonable"},
            {"k": "Section 20 Threshold", "v": "Consult if works > £250/unit"},
            {"k": "Ground Rent (new leases)", "v": "Peppercorn since Jun 2022"},
            {"k": "Buildings Insurance", "v": "Leaseholder can challenge"},
            {"k": "RICS Service Charge Code", "v": "Best practice guidance"},
            {"k": "Right to Manage", "v": "50% leaseholder participation"},
            {"k": "S20 Notice Period", "v": "30 days for observations"},
            {"k": "Residential Agent Reg.", "v": "ARMA / RICS preferred"},
        ],
        "Request Service",
    ),
    "98000": _entry(
        "Service Charge Calculator", "Annual Budget (£)", "Number of Units",
        "DIVIDE", "Charge Per Unit",
        "Residents Management Reference",
        [
            {"k": "LTA 1985 Section 19", "v": "Charges must be reasonable"},
            {"k": "Section 20 Threshold", "v": "Consult if works > £250/unit"},
            {"k": "Ground Rent Cap", "v": "Peppercorn (Leasehold Reform 2022)"},
            {"k": "Right to Manage", "v": "50% leaseholder participation"},
            {"k": "Sinking Fund", "v": "Optional but recommended"},
            {"k": "Management Company AGM", "v": "Required under articles"},
            {"k": "Accounts Filing", "v": "9 months after year-end (ltd)"},
            {"k": "Directors' Duties", "v": "Companies Act 2006 s.172"},
        ],
        "Contact Management",
    ),
    "68201": _entry(
        "Rental Yield Calculator", "Annual Rent (£)", "Property Value (£)",
        "PERCENT", "Gross Yield",
        "Lettings Reference",
        [
            {"k": "Tenant Fees Act 2019", "v": "No admin/referencing fees"},
            {"k": "Deposit Cap", "v": "5 weeks rent (< £50k pa)"},
            {"k": "EPC Minimum", "v": "Band E (planned Band C by 2028)"},
            {"k": "Gas Safety Check", "v": "Annual — CP12 certificate"},
            {"k": "EICR", "v": "Every 5 years (electrical)"},
            {"k": "Section 21 Notice", "v": "Abolished (Renters Reform Bill)"},
            {"k": "Smoke Alarm Requirement", "v": "Each storey with habitable room"},
            {"k": "HMO Licence Threshold", "v": "5+ occupants, 2+ households"},
        ],
        "Book a Viewing",
    ),

    # ── Courier / Logistics ───────────────────────────────────────────────────
    "53202": _entry(
        "Mileage Cost Calculator", "Miles Driven", "Fuel Cost per Mile (p)",
        "MULTIPLY", "Total Fuel Cost",
        "Courier Reference",
        [
            {"k": "HMRC Mileage Rate (car)", "v": "45p/mile (first 10,000)"},
            {"k": "HMRC Mileage (>10k)", "v": "25p/mile"},
            {"k": "Van AMAP Rate", "v": "45p/mile"},
            {"k": "Average Diesel (2024)", "v": "~149p/litre"},
            {"k": "Average Petrol (2024)", "v": "~147p/litre"},
            {"k": "Driver CPC Required", "v": "35 hrs periodic training"},
            {"k": "Tachograph Rules", "v": "3.5t+ vehicles on public roads"},
            {"k": "Daily Drive Limit", "v": "9 hours (10 hrs twice/week)"},
        ],
        "Request a Delivery",
    ),
    "49410": _entry(
        "Delivery Cost Calculator", "Distance (miles)", "Rate per Mile (£)",
        "MULTIPLY", "Trip Earnings",
        "Freight Reference",
        [
            {"k": "Operator Licence", "v": "Required 3.5t+ goods vehicles"},
            {"k": "Vehicle Excise Duty", "v": "Based on revenue weight"},
            {"k": "Tachograph Exemptions", "v": "< 100km radius, certain loads"},
            {"k": "Working Time Regs", "v": "Max 60 hrs/week (mobile workers)"},
            {"k": "Break Rules", "v": "45 min after 4.5 hrs driving"},
            {"k": "HGV Driver Hours", "v": "Max 9 hrs/day driving"},
            {"k": "HMRC Fuel Rates (HGV)", "v": "Advisory rates quarterly"},
            {"k": "Hire & Reward Insurance", "v": "Required for all carriage"},
        ],
        "Book a Collection",
    ),

    # ── Healthcare / Care ─────────────────────────────────────────────────────
    "87100": _entry(
        "Care Hours Calculator", "Hours of Care", "Hourly Rate (£)",
        "MULTIPLY", "Care Cost",
        "Care Home Reference",
        [
            {"k": "CQC Registration", "v": "Required for regulated activities"},
            {"k": "Nurse-to-Patient Ratio", "v": "No statutory ratio (England)"},
            {"k": "NMC Registration Fee", "v": "£120 / yr (nurses)"},
            {"k": "DBS Check", "v": "Enhanced DBS required (care roles)"},
            {"k": "Care Certificate", "v": "15 standards for new care staff"},
            {"k": "Deprivation of Liberty", "v": "DoLS authorisation required"},
            {"k": "Mental Capacity Act", "v": "Best interest decisions"},
            {"k": "Mandatory Training", "v": "Safeguarding, moving & handling"},
        ],
        "Enquire About Care",
    ),
    "88100": _entry(
        "Care Visit Calculator", "Visits per Week", "Cost per Visit (£)",
        "MULTIPLY", "Weekly Care Cost",
        "Domiciliary Care Reference",
        [
            {"k": "Minimum Visit Length", "v": "15 min (CQC guidance)"},
            {"k": "Care Workers — Min. Wage", "v": "£11.44/hr (NLW 2024)"},
            {"k": "Travel Time Pay", "v": "Must be paid at NMW"},
            {"k": "CQC Registration", "v": "Required for personal care"},
            {"k": "Care Certificate", "v": "15 standards (induction)"},
            {"k": "DBS Enhanced Check", "v": "Renewed every 3 years"},
            {"k": "Lone Worker Policy", "v": "Mandatory risk assessment"},
            {"k": "Safeguarding Referral", "v": "Local authority duty"},
        ],
        "Request a Care Plan",
    ),

    # ── Construction / Development ────────────────────────────────────────────
    "41100": _entry(
        "Stamp Duty Calculator", "Property Value (£)", "",
        "STAMP_DUTY", "SDLT Due",
        "Property Development Reference",
        [
            {"k": "SDLT Band 1", "v": "£0 – £250,000 → 0%"},
            {"k": "SDLT Band 2", "v": "£250,001 – £925,000 → 5%"},
            {"k": "SDLT Band 3", "v": "£925,001 – £1.5m → 10%"},
            {"k": "SDLT Band 4", "v": "Above £1.5m → 12%"},
            {"k": "Additional Dwelling", "v": "+3% surcharge (2nd home)"},
            {"k": "CIL Liability", "v": "Set by local authority"},
            {"k": "Building Regs", "v": "Full plans or building notice"},
            {"k": "Planning Permission", "v": "Required for material change"},
        ],
        "Discuss a Project",
    ),
    "41201": _entry(
        "Stamp Duty Calculator", "Property Value (£)", "",
        "STAMP_DUTY", "SDLT Due",
        "Housebuilder Reference",
        [
            {"k": "SDLT Band 1", "v": "£0 – £250,000 → 0%"},
            {"k": "SDLT Band 2", "v": "£250,001 – £925,000 → 5%"},
            {"k": "SDLT Band 3", "v": "£925,001 – £1.5m → 10%"},
            {"k": "SDLT Band 4", "v": "Above £1.5m → 12%"},
            {"k": "NHBC Warranty", "v": "10-year Buildmark standard"},
            {"k": "Help to Buy ISA", "v": "Replaced by Lifetime ISA"},
            {"k": "Shared Ownership", "v": "25%–75% initial share"},
            {"k": "Section 106", "v": "Planning obligations (affordable)"},
        ],
        "Request a Brochure",
    ),

    # ── Accounting / Finance ──────────────────────────────────────────────────
    "69201": _entry(
        "VAT Calculator", "Net Amount (£)", "",
        "VAT_ADD", "VAT-Inclusive Total",
        "Accounting Reference",
        [
            {"k": "Standard VAT Rate", "v": "20%"},
            {"k": "Reduced VAT Rate", "v": "5% (energy, children's car seats)"},
            {"k": "Zero Rate", "v": "Food, books, children's clothing"},
            {"k": "VAT Registration Threshold", "v": "£90,000 turnover (2024)"},
            {"k": "Corporation Tax", "v": "19% (< £50k profit) / 25% (> £250k)"},
            {"k": "Annual Investment Allowance", "v": "£1m per year"},
            {"k": "R&D Tax Credit (SME)", "v": "Enhanced 130% deduction"},
            {"k": "IR35 Assessment", "v": "Check employment status"},
        ],
        "Book a Consultation",
    ),
    "69202": _entry(
        "VAT Calculator", "Net Amount (£)", "",
        "VAT_ADD", "VAT-Inclusive Total",
        "Tax Reference",
        [
            {"k": "Standard VAT Rate", "v": "20%"},
            {"k": "VAT Registration Threshold", "v": "£90,000 (2024)"},
            {"k": "VAT Return Deadline", "v": "1 month + 7 days after period"},
            {"k": "Making Tax Digital", "v": "Required for VAT-registered"},
            {"k": "Corporation Tax Rate", "v": "25% (profits over £250,000)"},
            {"k": "Small Profits Rate", "v": "19% (profits up to £50,000)"},
            {"k": "Self-Assessment Deadline", "v": "31 January (online)"},
            {"k": "Penalty — Late Filing", "v": "£100 initial + daily (after 3m)"},
        ],
        "Book a Consultation",
    ),

    # ── Retail ────────────────────────────────────────────────────────────────
    "47190": _entry(
        "Retail Margin Calculator", "Cost Price (£)", "Margin (%)",
        "PERCENT", "Profit on Cost",
        "Retail Reference",
        [
            {"k": "National Living Wage", "v": "£11.44/hr (Apr 2024)"},
            {"k": "Business Rates Relief", "v": "100% for RV ≤ £12,000"},
            {"k": "Retail Discount (2024)", "v": "75% off for RV ≤ £51,000"},
            {"k": "VAT Standard Rate", "v": "20%"},
            {"k": "Packaging Waste", "v": "PRN compliance required"},
            {"k": "Consumer Rights Act", "v": "30-day right to reject"},
            {"k": "GDPR — Customer Data", "v": "Lawful basis required"},
            {"k": "PCI DSS", "v": "Card payment data security"},
        ],
        "Shop Now",
    ),
}

# ── 2-digit prefix fallbacks ──────────────────────────────────────────────────

_PREFIX: dict[str, dict] = {
    "47": _EXACT["47190"],   # All retail
    "49": _EXACT["49410"],   # Road/rail transport
    "52": _EXACT["53202"],   # Storage & support for transport
    "53": _EXACT["53202"],   # Post/courier
    "64": _entry(
        "Interest Calculator", "Principal Amount (£)", "Annual Rate (%)",
        "PERCENT", "Annual Interest",
        "Financial Services Reference",
        [
            {"k": "FCA Authorisation", "v": "Required for regulated activities"},
            {"k": "Consumer Duty", "v": "In force July 2023"},
            {"k": "FSCS Protection", "v": "Up to £85,000 per person"},
            {"k": "Financial Promotions", "v": "Must be approved by FCA auth. firm"},
            {"k": "AML Registration", "v": "Required for most FS businesses"},
            {"k": "Base Rate (BoE)", "v": "Check bankofengland.co.uk"},
            {"k": "CASS Rules", "v": "Client money segregation"},
            {"k": "MiFID II", "v": "Investment firm obligations"},
        ],
    ),
    "68": _EXACT["68201"],   # Real estate activities
    "69": _EXACT["69201"],   # Legal & accounting
    "78": _EXACT["78200"],   # Employment activities
    "86": _EXACT["87100"],   # Human health
    "87": _EXACT["87100"],   # Residential care
    "88": _EXACT["88100"],   # Social work
    "98": _EXACT["98000"],   # Residents bodies
}

# ── Default ───────────────────────────────────────────────────────────────────

_DEFAULT = _entry(
    "Hours & Rate Calculator", "Hourly Rate (£)", "Hours",
    "MULTIPLY", "Total Amount",
    "Business Reference",
    [
        {"k": "Corporation Tax", "v": "19%–25% (2024)"},
        {"k": "VAT Standard Rate", "v": "20%"},
        {"k": "VAT Registration", "v": "£90,000 turnover threshold"},
        {"k": "National Living Wage", "v": "£11.44/hr (25+, Apr 2024)"},
        {"k": "Annual Investment Allow.", "v": "£1,000,000 / year"},
        {"k": "Small Business Rates Relief", "v": "100% for RV ≤ £12,000"},
        {"k": "Companies House Filing", "v": "Annual confirmation statement"},
        {"k": "PAYE Registration", "v": "Required before first payroll"},
    ],
)


# ── Public API ────────────────────────────────────────────────────────────────

def lookup(sic_codes: str) -> dict:
    """Return the best-match SIC data for a comma-separated SIC code string."""
    primary = (sic_codes.split(",")[0].strip() if sic_codes else "").strip()

    # 1. Exact 5-digit match
    if primary in _EXACT:
        return _EXACT[primary]

    # 2. 2-digit prefix
    prefix = primary[:2]
    if prefix in _PREFIX:
        return _PREFIX[prefix]

    return _DEFAULT
