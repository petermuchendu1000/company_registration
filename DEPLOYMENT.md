# Deployment runbook

Every figure here was measured or read from an API in the session that
produced this file. Where something is unverified it says so, rather than
reading as fact.

## What I could not do

I could not create the server. No Vultr credential exists in the build
environment, no provisioning CLI is installed, and `api.vultr.com/v2/account`
answers `401`. Creating it also needs the payment method on the account.

A write-scoped API token would be a larger grant than a three-minute console
operation justifies, so **create the instance yourself** and run this runbook
on it.

## 1. The instance

Read from `api.vultr.com/v2/plans` [VERIFIED]:

| field | value |
|---|---|
| plan | `vc2-2c-2gb` |
| price | $15/month |
| vCPU | 2 |
| RAM | 2048 MB |
| disk | 65 GB NVMe |
| bandwidth | 3072 GB |
| region | `lhr` (London) — confirmed available for this plan |

OS: **Debian 12**. The code needs no 3.10-only syntax — no `match`
statements and no `except*` anywhere, and 18 files carry
`from __future__ import annotations` — so Debian 12's Python 3.11 is
sufficient. [INFERRED from those greps; only 3.13 is [VERIFIED], since that
is what the build environment ran.]

**London, not Johannesburg**, even though you are in Nairobi. The server
talks to Companies House, Namecheap and D&B far more than you click, and a
dashboard does not care about 140 ms. A UK-origin IP is also the least
anomalous source for UK company lookups.

### Why 2 vCPU and not the $10 single-core

Measured in the build environment:

| | peak RSS |
|---|---|
| headless Chromium | 742 MB |
| **headed Chromium + Xvfb** | **1117 MB** |
| gunicorn master + worker | 75 MB |

Budget: 1117 + 75 + ~200 (Debian, [INFERRED]) ≈ **1.4 GB of 2 GB**. The
second core matters because headed Chromium under Xvfb is CPU-bound while
rendering and would otherwise contend with the dashboard. Add swap anyway
(step 2) — it costs nothing and the browser is the spiky part.

## 2. Base setup

```bash
# as root on the new instance
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl xvfb

# 2 GB swap: headroom for the headed browser, not a substitute for RAM
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h          # verify: Swap total shows 2.0Gi
```

## 3. Application

```bash
install -d -o root -g root /opt/company_registration
cd /opt
git clone <your-repo-url> company_registration
cd company_registration
git checkout claude/upbeat-newton-6u6jwz

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Installs Chromium AND its system libraries. --with-deps is the documented
# flag for the OS packages; chromium pulls the full browser, which the DUNS
# path needs because it runs headed.
.venv/bin/playwright install --with-deps chromium
```

## 4. Environment file

`/opt/company_registration/.env`, mode `600`. Every name below was read out
of the source, not guessed:

```ini
# Required, or the app serves nothing but /health. Generate with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
APP_AUTH_TOKEN=
FLASK_SECRET_KEY=

DATABASE_URL=            # Supabase SHARED POOLER url - see below. Not the
                         # direct db.<ref>.supabase.co host, which will not
                         # resolve on this server. SUPABASE_DB_URL also read.
COMPANIES_HOUSE_API_KEY=

NAMECHEAP_API_USER=
NAMECHEAP_API_KEY=
NAMECHEAP_USERNAME=
NAMECHEAP_CLIENT_IP=     # see step 5 - MUST be this server's IP
NAMECHEAP_SANDBOX=1      # keep 1 until you have deliberately tested live
NAMECHEAP_DEFAULT_ADDRESS_ID=
NAMECHEAP_REFERENCE_DOMAIN=

XBINDER_EMAIL=
XBINDER_PASSWORD=

LOG_LEVEL=INFO           # INFO switches on browser_stealth's diagnostics
DB_POOL_MAX=16           # must exceed gunicorn threads (8) plus stage threads
```

`NAMECHEAP_SANDBOX=1` deliberately. Domain registration spends money; turn
it off only when you intend a real purchase.

### Use the shared pooler DSN, not the direct host

Per Supabase's own documentation, a **direct** connection to
`db.<project-ref>.supabase.co:5432` is **IPv6-only** unless the project
carries the paid IPv4 add-on. A Vultr instance gets an IPv4 address, so a
direct DSN fails there. Confirmed by observation: that hostname returns no
record from an IPv4-only network, while the project's own
`<project-ref>.supabase.co` and the pooler hosts both resolve normally — so
it is not a general DNS problem.

The **shared pooler is IPv4 on every plan**. Two differences from the
direct string matter, and the second is easy to miss:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
             ^^^^^^^^^^^^^^^^^^^^^                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
             username becomes postgres.<ref>,     pooler host, and the region
             NOT plain postgres                   is not guessable - copy it
```

Use **port 5432 (session mode)**, which is Supabase's stated guidance for
IPv4-only networks, not 6543 (transaction mode). Session mode keeps one
backend per client connection, which is what `db._pool()` expects; stacking
a transaction pooler under an application pool is the wrong shape and
transaction mode also drops prepared statements.

Copy the exact string from the Supabase dashboard: Project Settings →
Database → Connection string → **Session pooler**. The region index in the
hostname varies by project and cannot be guessed.

Keep an eye on `DB_POOL_MAX` against your plan's connection limit: session
mode holds one backend per pooled connection, so 16 here means up to 16
backends held by this app.

## 5. Namecheap IP allowlist — do this before any domain stage

The Namecheap API rejects calls from an un-allowlisted IP, and the new
server has a different IP from your laptop.

```bash
cd /opt/company_registration
.venv/bin/python check_ip.py     # reports this host's public IP and whether
                                 # Namecheap accepts it, and rewrites
                                 # NAMECHEAP_CLIENT_IP in .env
```
Then add that IP at Namecheap: Profile → Tools → API Access → Manage.

Skipping this does not fail quietly in a harmless way — the domain stage
will error, and with the purchase-claim machinery in place it will leave the
company's `domain_status` needing a manual look.

## 6. Display for the DUNS browser path

The dashboard's own DUNS stage needs **no** display: it goes through
`run_pipeline.get_duns_number`, which is a plain HTTP call to D&B's JSON
endpoint. The **wizard's** DUNS button (`POST /api/duns/request`) and the
stealth endpoints in `app.py` drive a real browser, forced headed because
D&B blocks headless.

Two units rather than wrapping gunicorn in `xvfb-run`: a long-running daemon
under `xvfb-run` complicates signal handling and restarts, and a standalone
Xvfb gives the app a stable `DISPLAY` across restarts.

`/etc/systemd/system/xvfb.service`:
```ini
[Unit]
Description=Virtual framebuffer for the DUNS browser path
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24
Restart=always

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/companyreg.service`:
```ini
[Unit]
Description=company_registration dashboard
After=network.target xvfb.service
Requires=xvfb.service

[Service]
WorkingDirectory=/opt/company_registration
EnvironmentFile=/opt/company_registration/.env
Environment=DISPLAY=:99
ExecStart=/opt/company_registration/.venv/bin/gunicorn -c gunicorn.conf.py app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now xvfb companyreg
systemctl status companyreg --no-pager
```

`workers = 1` in `gunicorn.conf.py` is a correctness constraint, not tuning:
the job table lives in process memory. Do not raise it.

**Do not run `python app.py` while the service is up.** Its `__main__`
loops over ten ports, so it will not refuse to start — it binds a different
one and you end up with two app processes. `duns_requests.json` is now
guarded by an `fcntl.flock` on a `duns_requests.json.lock` sidecar, so the
two cannot corrupt the store or lose each other's records, but the second
process still has its own in-memory job table and the dashboard will
disagree with itself about what is running. Use `systemctl stop companyreg`
first.

If a DUNS operation ever fails with "could not lock
duns_requests.json.lock", that is this situation: something else holds the
lock. Find the second process. The lock is released by the kernel when a
process exits, so a crash cannot leave it stuck.

## 7. Access — do not expose this publicly

`gunicorn.conf.py` binds `127.0.0.1:8000` on purpose. Reach it over
Tailscale instead of opening a port:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
tailscale ip -4        # the address you browse to, port 8000
```

You get no public attack surface, no TLS certificate and no reverse proxy,
and the operator token becomes defence-in-depth rather than the only
defence. For a single-operator system holding director dates of birth and a
live payment credential, that is the right shape.

## 8. Database schema

First start runs `ensure_schema`, which now reconciles columns with
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. It will **add `short_name` to
your live Supabase table.** Additive, nullable, metadata-only in Postgres —
no table rewrite. Rollback is `DROP COLUMN short_name`.

## 9. The spreadsheet import

Both UIs now read Postgres, so **any company that exists only in your Excel
file will not appear** until it is imported.

`import_from_excel` was **not safe to run** as originally written, and this
was measured, not suspected. It included a column whenever the HEADER was
present, whatever the cell held, and `_norm` maps `""` to `None`, so every
blank cell wrote NULL over a live value. Against a sheet with blank domain
columns:

```
domain_status  'purchasing'       -> None    (purchase claim destroyed)
domain         'alreadybought.uk' -> None    (owned domain erased)
then: claim_domain_purchase() -> True        (a second billed order allowed)
      _compute_stages domain   -> False      (run-all would buy again)
```

It now skips blank cells, so an import can add rows and fill gaps but never
erase. **A populated cell still overwrites a populated column** — that is
what an import is for — so the sheet must be right about anything it does
fill in.

### Always diff before importing

`python db.py --diff` is read-only and shows the field-level consequences.
It shares its parse with `import_from_excel`, so it cannot drift from the
operation it previews.

```bash
cd /opt/company_registration
.venv/bin/python db.py --diff           # defaults to the sheet path below
.venv/bin/python db.py --diff /path/to/other.xlsx
```

Real output against a seeded database:

```
  companies the sheet would create : 1
  fields it would fill in          : 2
  populated fields it would REPLACE: 2
  fields already matching          : 4
  row renumbering ('No.' only)     : 0 (cosmetic)

  new: 92000003

  Overwrites, by company. Each line replaces a value that is already set:
    92000001
      Domain Status: 'purchasing' -> 'registered'  <-- MONEY
    92000002
      Domain: 'old.uk' -> 'new.uk'  <-- MONEY

  STOP. 1 company(ies) hold an outstanding purchase claim ('purchasing')
  that this sheet would overwrite:
    92000001
  A domain may already have been bought and charged for these. Importing
  discards the record of it and allows a second purchase. Reconcile against
  Namecheap first.
```

Read it in this order:

1. **`STOP` block, if present.** A company holding a `'purchasing'` claim
   means a domain may already have been bought and charged while its
   outcome went unrecorded. Importing over it discards that record and lets
   the domain stage buy again. Reconcile against the Namecheap account
   first; do not import.
2. **Lines marked `<-- MONEY`.** `Domain`, `Domain Status`, `Domain Cost`
   and `DUNS Number`. A wrong value here costs money or re-runs a stage.
3. **Everything else under Overwrites.** The sheet replaces a value that is
   already set. Only you can tell stale from authoritative.
4. **`new` and "fields it would fill in".** These are the point of the
   exercise and are safe — nothing is lost.

Then, once it reads correctly:

```bash
.venv/bin/python db.py --import
# or: .venv/bin/python db.py --import /path/to/other.xlsx
```

And confirm afterwards that no claim was disturbed — expect the same list
you saw before the import:

```bash
.venv/bin/python -c "
import db
print('outstanding purchase claims:',
      [r['Company Number'] for r in db.all_rows(use_cache=False)
       if r.get('Domain Status') == db.DOMAIN_CLAIM])"
```

## 10. Verification — each step can fail

```bash
# 1. health, without credentials
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
#    expect 200

# 2. the gate actually bites
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v2/companies
#    expect 401 - if this returns 200, APP_AUTH_TOKEN is not set correctly

# 3. authenticated access works
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $APP_AUTH_TOKEN" http://127.0.0.1:8000/api/v2/companies
#    expect 200

# 4. request correlation and the money channel are live
journalctl -u companyreg -n 30 --no-pager | grep -E '\[app\]|\[money\]'
#    expect lines like: INFO [app] [a1b2c3d4] GET /health -> 200 in 1ms

# 5. a headed browser can actually start (the DUNS precondition)
DISPLAY=:99 .venv/bin/python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False); b.close(); print('headed OK')"
#    expect 'headed OK'. Without DISPLAY it raises a RuntimeError naming
#    xvfb-run, which is the preflight working as intended.

# 6. Namecheap accepts this host
.venv/bin/python check_ip.py
```

## The one risk this runbook cannot retire

Whether D&B blocks this datacenter IP is **[UNKNOWN]**. No real DUNS run has
been made from any server. `browser_stealth.py` has no proxy support, so
there is no fallback in code if it is blocked. The first real wizard DUNS
request on this box is the test. Budget for a residential proxy only once
you have evidence you need one — and note the $15 plan leaves no room for
one, so that would be an increase, not a reallocation.
