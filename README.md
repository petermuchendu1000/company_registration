# Company App Factory

Takes a UK company number and produces a registered domain, an assigned
mailbox, an incorporation certificate, a generated Android app and a
validated Google Play listing — for many client tenants on one shared host.

The design, the isolation model and the known limits are in
[ARCHITECTURE.md](ARCHITECTURE.md). Start there before changing anything
structural.

---

## Run it

```bash
cp .env.example .env
# fill in DATABASE_URL and FLASK_SECRET_KEY at minimum; generate a key with:
#   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'

docker compose up -d --build
docker compose logs -f app
```

The dashboard is then on `http://localhost/app` (or your `SITE_ADDRESS`).

You need a user before you can sign in:

```bash
docker compose exec app python3 -c "
import db
tid = db.create_tenant('acme', 'Acme Ltd')
db.create_user(tid, 'you@example.com', 'a-long-password', 'operator')
print('tenant', tid)
"
```

## See what is configured

```bash
docker compose run --rm app check
```

It lists every setting, says whether it is set — never its value — and maps
each one to the stages it unlocks:

```
  Stages
    OK details        ready
    -- domain         needs NAMECHEAP_API_KEY, NAMECHEAP_API_USER, ...
```

Exit codes: `0` fine, `1` some stage unavailable, `2` cannot start.

A half-configured instance is normal and runs fine — stages you have no
credentials for are simply unavailable, not broken.

## Run the pipeline from the CLI

```bash
docker compose run --rm app pipeline --stage details --company 12345678
docker compose run --rm app pipeline --tenant acme            # every stage
```

Releasing to Google Play is deliberately CLI-only. The dashboard validates
against a draft edit and discards it; it never publishes:

```bash
docker compose run --rm app pipeline --stage publish --company 12345678 --commit
```

---

## Without Docker

```bash
pip install -r requirements.txt
cp .env.example .env          # and fill it in
./launch.sh                   # preflight, then serve
./launch.sh --check           # preflight only
```

`launch.sh` runs a single process on purpose; see ARCHITECTURE.md §6.

---

## Tests

```bash
export DATABASE_URL=postgresql://...
for t in test_*.py; do python3 "$t"; done
```

The suites needing Postgres **skip** without `DATABASE_URL` rather than
fail — check the output for "skipping" before reading a pass as meaningful.

`test_pipeline_e2e.py` runs all eight stages through the real HTTP API with
the external services stubbed at their boundaries; it covers the
orchestration, not the integrations.

---

## Spending money by accident

Two stages reach services that cost or cannot be undone:

- **`domain`** registers a real domain. Keep `NAMECHEAP_SANDBOX=1` until you
  mean it. `NAMECHEAP_MIN_BALANCE` stops a batch draining the account.
- **`publish --commit`** burns a version code permanently. It is refused
  unless run as its own stage.
