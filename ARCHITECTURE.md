# Architecture

How the company app factory is put together: what the pieces are, where the
boundaries sit, and which parts are deliberate rather than accidental.

For the settings themselves see `.env.example`, or run `python config.py check`.

---

## 1. What it does

A UK company goes in as a company number. What comes out is a registered
domain, an assigned mailbox, a downloaded incorporation certificate, a
generated Android app and a validated Google Play listing.

Eight stages, in the order the dashboard runs them:

| # | Stage | External service | Notes |
|---|-------|------------------|-------|
| 1 | `details` | Companies House | Name, status, address, SIC codes, officers |
| 2 | `duns` | Dun & Bradstreet | Public lookup endpoint, no credential |
| 3 | `certificate` | Companies House | Incorporation PDF |
| 4 | `domain` | Namecheap | **Spends money.** Registers a real domain |
| 5 | `email` | — (internal pool) | Assigns a mailbox, sets `dev@` forwarding |
| 6 | `director_id` | XBINDER | Optional; skips cleanly when unconfigured |
| 7 | `app` | Gradle + Android SDK | Builds a signed APK and AAB |
| 8 | `publish` | Google Play | Validate by default; release only from the CLI |

---

## 2. Components

```
                    ┌──────────────────────────────┐
   browser ────────▶│  Caddy          :80 / :443   │  TLS, compression,
                    │                              │  security headers
                    └──────────────┬───────────────┘
                                   │ http
                    ┌──────────────▼───────────────┐
                    │  app            :5000        │
                    │                              │
                    │  auth.py       deny-by-      │
                    │                default gate  │
                    │  app.py        shell + pages │
                    │  api_v2.py     dashboard API │
                    │  wizard_routes onboarding    │
                    │                              │
                    │  ── worker threads ───────   │
                    │  pipeline.py   stage bodies  │
                    │  run_pipeline  CH, Namecheap │
                    │  play_publisher  Play API    │
                    │  apps/generator  Gradle      │
                    └───┬──────────────────────┬───┘
                        │                      │
            ┌───────────▼────────┐  ┌──────────▼─────────────┐
            │  PostgreSQL        │  │  PIPELINE_OUTPUT_DIR   │
            │                    │  │                        │
            │  tenants           │  │  companies/            │
            │  users             │  │    {tenant-slug}/      │
            │  companies         │  │      {cn} - {NAME}/    │
            │  jobs              │  │        certificate/    │
            │  email_pool        │  │        director_id/    │
            └────────────────────┘  │        app/artifacts/  │
                                    └────────────────────────┘
```

Everything above runs in **one application process**. That is a decision, not
an oversight — see §6.

---

## 3. Tenancy

The isolation model is a single shared instance with per-tenant scoping, not
a process or database per client.

**In the database.** Every row is keyed by tenant. Companies use a composite
primary key `(tenant_id, company_number)`, so two tenants may legitimately
hold the same UK company number and neither can see the other's row.

**In the process.** The signed-in user's tenant is bound to a
`contextvars.ContextVar` by the auth layer and cleared on request teardown —
WSGI reuses worker threads, and a binding left set would carry one client's
tenant into the next request that thread handles.

> A `ContextVar` is **not** inherited by a thread you spawn. Stage work runs
> in worker threads, so `_run_tracked` rebinds the tenant explicitly with
> `db.tenant_scope(tenant_id)`. Remove that and every stage fails with
> "No tenant bound" — which `test_pipeline_e2e.py` proves by mutation.

**On disk.** `company_paths` is the only definition of where a company's files
live:

```
{PIPELINE_OUTPUT_DIR}/companies/{tenant-slug}/{cn} - {NAME}/
```

Each company directory also carries a `.company.json` stamp naming its owner,
so a directory that arrives by some other route — a restored backup, a copy
between machines — can still be told apart from one this tenant created.

**`SINGLE_TENANT_SLUG` is a loaded gun on a multi-tenant instance.** It
resolves a tenant whenever none is bound, which means any code path that
fails to bind one silently gets that tenant instead of failing. Set it only
for a genuinely single-client deployment.

---

## 4. The job registry

Stage runs are rows in Postgres, not in-memory state, so a restart does not
lose track of what was running.

- **Claiming** is a single conditional upsert with
  `WHERE status NOT IN ('running','queued') RETURNING 1`. One statement, so
  two clicks landing together cannot both start the same stage.
- **Statuses** are `queued` → `running` → `done` | `failed`. Only `queued`
  and `running` count as active.
- **Heartbeats** are written while a stage runs; `reclaim_stale_jobs()` at
  startup fails any row still marked active that this worker owned, so a
  killed process does not leave a stage stuck "running" forever.
- **Output** from each stage is captured per job, via a stdout tee keyed by
  thread, and stored on the row. This is why the dashboard can show a live
  log for a job it did not start.
- **Run-all** walks the stages in order and **stops at the first failure**.
  Later stages are left untouched rather than run against incomplete inputs.

The email pool uses `FOR UPDATE SKIP LOCKED` so concurrent assignments cannot
hand the same mailbox to two companies.

---

## 5. Trust boundaries

| Boundary | Control |
|---|---|
| Internet → app | Caddy terminates TLS; HSTS, CSP, `nosniff`, `frame-ancestors 'none'` |
| Anonymous → app | `before_request` denies everything not in `PUBLIC_ENDPOINTS` (login, logout, `/health`). A route added later is protected the moment it exists |
| Tenant → tenant | Composite keys in the database; per-tenant subtree plus ownership stamp on disk |
| Tenant → files | `auth.tenant_company_file` requires all three: the path resolves inside *this tenant's* subtree, the tenant holds a row for that company number, and the directory stamp names this tenant |
| App → Play | Per-company service account JSON, mounted read-only. These keys can publish to the store |

Secrets never enter the image: `.dockerignore` excludes `.env`, `secrets/`,
`*.jks`, `*service_account*.json` and `*.xlsx`. Deleting a file in a later
layer does not remove it from an earlier one, so keeping it out of the
context is the only reliable control.

---

## 6. Why one process

`docker-entrypoint.sh` runs gunicorn with `--workers 1 --threads 8`. Two
pieces of state live in process memory and would break if split:

1. **`_BUILD_SLOTS`**, the semaphore capping concurrent Gradle builds
   (`MAX_CONCURRENT_BUILDS`). A second worker would silently double build
   concurrency, and Gradle wants a gigabyte or more of heap per build.
2. **The live log tail.** Output is tee'd per thread in memory until the job
   finishes. A second worker would serve empty logs for jobs the first owns.

Scale with threads, not workers. Scaling beyond one box means moving the job
queue out of Postgres-as-registry into a real broker and making the build
host a separate service — a change of shape, not a config flag.

---

## 7. Deployment

```
docker compose up -d --build
docker compose run --rm app check      # what is configured, and what it unlocks
docker compose logs -f app
```

**Services.** `db` (Postgres 16, not published — reachable only over the
compose network), `app`, `proxy` (Caddy).

**Volumes.** `db-data`, `app-data` (`/data`, holds `pipeline_output`),
`gradle-cache`, `caddy-data`, `caddy-config`. The app volume grows with every
build and must survive a redeploy.

**Startup order.** `app` waits for the database healthcheck, then the
entrypoint validates configuration (exit 2 names the missing setting), waits
for Postgres to accept connections, creates the schema, migrates any
pre-tenant company directory into its owner's subtree, and only then starts
gunicorn. Under gunicorn the app's `__main__` block never runs, which is why
that work lives in the entrypoint.

**Memory.** The `app` service is capped (`APP_MEMORY_LIMIT`, default 6g) so a
runaway Gradle build cannot take the database down with it.

---

## 8. Before going live

- [ ] `SITE_ADDRESS` set to the real hostname, so Caddy issues a certificate
- [ ] `SESSION_COOKIE_SECURE=1` (compose sets it; keep it)
- [ ] `FLASK_DEBUG` unset or `0` — it is remote code execution on a public host
- [ ] `FLASK_SECRET_KEY` at least 32 random characters, unique to this deployment
- [ ] `NAMECHEAP_CLIENT_IP` is the **VPS egress IP**, not a laptop's
- [ ] `NAMECHEAP_SANDBOX=1` until you intend to spend money
- [ ] `SINGLE_TENANT_SLUG` unset unless this really is one client
- [ ] A backup for `db-data` and `app-data`
- [ ] Signing keys out of version control (see §9)

---

## 9. Known limits

These are real and currently unaddressed. Listed so nobody rediscovers them
the hard way.

- **Signing passwords are committed** in
  `apps/template-shift-journal/app/build.gradle.kts`. `policy_gate` flags this
  as a `signing_secrets_in_vcs` blocker. This is the one outstanding item that
  is a live security problem rather than a quality one.
- **Store screenshots are synthetic** — every app ships the same mock, which
  is a Play metadata rejection waiting to happen. Generating real ones needs a
  device or emulator.
- **Old PII is still fetchable by commit SHA** after a force-push; only GitHub
  Support can purge it.
- **`requirements.txt` is unpinned**, so two builds a month apart are not the
  same image.
- **The `email` stage logs nothing**, so its log drawer reads "No output yet."
  even though it did work. Pinned in `test_pipeline_e2e.py`.
- **`director_id` is unmaintained.** It is contained rather than fixed: it
  absorbs its own failures so it cannot stop the stages after it, which
  `test_director_id_containment.py` enforces.
- **Horizontal scale is not available** without the redesign in §6.

---

## 10. Tests

| File | Covers |
|---|---|
| `test_pipeline_e2e.py` | All eight stages through the real HTTP API, job registry, failure path, tenant isolation |
| `test_company_paths.py` | Per-tenant directories, ownership stamps, the download guard, migration |
| `test_job_tenancy.py`, `test_jobs_store.py` | Tenant rebinding in threads; claim/update/reclaim |
| `test_email_pool.py` | Concurrent assignment under `SKIP LOCKED` |
| `test_stage_match.py`, `test_stage_email.py`, `test_details.py`, `test_certificate.py`, `test_domain_guard.py` | Per-stage behaviour |
| `test_director_id_containment.py` | That the unmaintained stage cannot break the run |

The suites needing Postgres skip cleanly without `DATABASE_URL` — check for
"skipping" in the output before reading a pass as meaningful.
