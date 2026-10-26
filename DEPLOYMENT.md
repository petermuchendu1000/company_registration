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

DATABASE_URL=            # Supabase pooler URL; SUPABASE_DB_URL also accepted
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

```bash
# Dry-run first: see what is only in the sheet before writing anything.
.venv/bin/python - <<'PY'
import db, openpyxl
wb = openpyxl.load_workbook('pipeline_output/companies_pipeline.xlsx',
                            data_only=True, read_only=True)
ws = wb.active
it = ws.iter_rows(values_only=True)
hdr = [str(v or '').strip() for v in next(it)]
i = hdr.index('Company Number')
sheet = {db._cn8(r[i]) for r in it if r and r[i]}
have = db.company_numbers()
print('in sheet      :', len(sheet))
print('in Postgres   :', len(have))
print('sheet only    :', sorted(sheet - have))
print('Postgres only :', sorted(have - sheet))
PY

# Then, once the above looks right:
.venv/bin/python -c "import db; print(db.import_from_excel('pipeline_output/companies_pipeline.xlsx'))"
```

Check afterwards that no claim was disturbed:

```bash
.venv/bin/python -c "
import db
rows = db.all_rows()
print('rows with an outstanding purchase claim:',
      [r['Company Number'] for r in rows
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
