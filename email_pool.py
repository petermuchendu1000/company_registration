"""
Email pool — the Gmail accounts a tenant owns, one assigned per company.

Backed by Postgres, per tenant. It used to be a shared emails.xlsx keyed by
company number alone, which had two faults:

  * No tenant isolation. Two clients holding the same company number shared
    an assignment, and both drew from the same list, so one client's work
    consumed the other's accounts and could read back their address.
  * No usable locking. Every caller constructed its own EmailPool, so the
    instance lock guarded nothing, and assigning rewrote the entire workbook.
    Eight concurrent assignments against the old implementation produced one
    assignment and seven `BadZipFile: Truncated file header` crashes — readers
    hitting a half-written file. Stages run in threads, so that was reachable.

Load a tenant's accounts once from the spreadsheet they came in:

    python email_pool.py import --tenant acme --file emails.xlsx
    python email_pool.py status --tenant acme

Usage is unchanged for callers:

    pool = EmailPool()                      # binds to the current tenant
    email, first, last = pool.assign_next(company_number="12345678")
"""

import os
import re

BASE_DIR = os.path.dirname(__file__)
EMAILS_FILE = os.path.join(BASE_DIR, "emails.xlsx")


def _db():
    import db
    return db


def extract_name_from_email(email):
    """Extract a plausible first and last name from an email address.

    Examples:
        janedoherty@gmail.com   -> ("Jane", "Doherty")
        john.doe@gmail.com       -> ("John", "Doe")
        jsmith123@gmail.com      -> ("Jsmith", "User")
        a@gmail.com              -> ("A", "User")
    """
    local = email.split("@")[0]

    # Remove trailing digits
    local_clean = re.sub(r'\d+$', '', local)
    if not local_clean:
        local_clean = local

    # Remove leading/trailing underscores/dots/hyphens
    local_clean = local_clean.strip("._-")

    # Split on common separators
    parts = re.split(r'[._\-]+', local_clean)
    parts = [p for p in parts if p]

    if len(parts) >= 2:
        first_name = parts[0].capitalize()
        last_name = parts[-1].capitalize()
    elif len(parts) == 1:
        name = parts[0]
        # Try to split camelCase
        camel_parts = re.findall(r'[A-Z]?[a-z]+', name)
        if len(camel_parts) >= 2:
            first_name = camel_parts[0].capitalize()
            last_name = camel_parts[-1].capitalize()
        elif len(name) > 4:
            # Split roughly in half
            mid = len(name) // 2
            first_name = name[:mid].capitalize()
            last_name = name[mid:].capitalize()
        else:
            first_name = name.capitalize()
            last_name = "User"
    else:
        first_name = "Account"
        last_name = "User"

    return first_name, last_name


class PoolExhausted(RuntimeError):
    """Every address in this tenant's pool is already assigned."""


class EmailPool:
    """One tenant's Gmail accounts. Thin wrapper over the db functions."""

    def __init__(self, tenant_id=None, emails_file=None):
        # emails_file is accepted so the import path and older callers keep
        # working; it is only read by import_from_file().
        self.emails_file = emails_file or EMAILS_FILE
        self._tenant_id = tenant_id

    @property
    def tenant_id(self):
        if self._tenant_id is None:
            self._tenant_id = _db().current_tenant_id()
        return self._tenant_id

    # ── pool contents ──────────────────────────────────────────────────────

    def import_from_file(self, path=None):
        """Load addresses from an .xlsx with an Email column. Returns new count."""
        import openpyxl

        path = path or self.emails_file
        if not os.path.exists(path):
            raise FileNotFoundError(f"Emails file not found: {path}")

        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        headers = [str(c.value or "").strip().lower()
                   for c in next(ws.iter_rows(min_row=1, max_row=1))]
        email_col = headers.index("email") if "email" in headers else 0

        emails = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            value = row[email_col] if email_col < len(row) else None
            if isinstance(value, str) and value.strip():
                emails.append(value.strip())
        wb.close()
        return _db().import_emails(self.tenant_id, emails)

    def _counts(self):
        return _db().email_pool_status(self.tenant_id)

    @property
    def total(self):
        return self._counts()["total"]

    @property
    def used_count(self):
        return self._counts()["used"]

    @property
    def available_count(self):
        return self._counts()["available"]

    # ── assignment ─────────────────────────────────────────────────────────

    def get_assigned_email(self, company_number):
        """(email, first, last) for a company that already has one, else Nones."""
        row = _db().assigned_email(self.tenant_id, company_number)
        if not row:
            return None, None, None
        first, last = extract_name_from_email(row["email"])
        return row["email"], first, last

    def assign_next(self, company_number=None, company_name=""):
        """
        Assign the next free address. Returns (email, first, last).

        Idempotent per company. Raises PoolExhausted when nothing is free,
        which is a RuntimeError as before, so existing handlers still catch it.
        """
        if not company_number:
            raise ValueError(
                "company_number is required: an address is assigned to a company, "
                "and without one the assignment cannot be looked up again."
            )
        row = _db().assign_email(self.tenant_id, company_number, company_name)
        if not row:
            counts = self._counts()
            raise PoolExhausted(
                f"No available emails: all {counts['total']} addresses in this "
                f"tenant's pool are assigned. Import more with "
                f"`python email_pool.py import --tenant <slug> --file <xlsx>`."
            )
        first, last = extract_name_from_email(row["email"])
        return row["email"], first, last

    def available(self, limit=50):
        """Addresses not yet assigned, in the order they will be handed out."""
        return _db().available_emails(self.tenant_id, limit)

    def release(self, company_number):
        """Return a company's address to the pool."""
        return _db().release_email(self.tenant_id, company_number)

    def status(self):
        counts = self._counts()
        print(f"Email Pool: {counts['total']} total, {counts['used']} assigned, "
              f"{counts['available']} available")
        return counts


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Email pool admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="load addresses from an .xlsx into a tenant's pool")
    imp.add_argument("--tenant", required=True, help="tenant slug")
    imp.add_argument("--file", default=EMAILS_FILE)
    st = sub.add_parser("status", help="show pool counts for a tenant")
    st.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)

    db = _db()
    tenant = db.get_tenant_by_slug(args.tenant)
    if not tenant:
        raise SystemExit(f"No such tenant: {args.tenant}")
    db.ensure_schema()
    pool = EmailPool(tenant_id=tenant["id"])

    if args.cmd == "import":
        added = pool.import_from_file(args.file)
        print(f"Imported {added} new address(es) into '{args.tenant}'.")
    pool.status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
