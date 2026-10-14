# SQL Server Diagnostic Assistant

An on-demand, **read-only** assistant that helps DBAs diagnose SQL Server and
application performance incidents. A DBA signs in, enters a SQL Server
connection, reviews and approves the diagnostic query batch, and receives
evidence, likely causes, validation checks, and remediation guidance.

The product never runs remediation and never stores credentials — it only
collects read-only evidence and recommends next steps.

> **Current state:** the backend is a FastAPI service backed by a SQL database
> (SQLite by default, selected with `DATABASE_URL`) that implements the API
> contract in [`openapi.yaml`](openapi.yaml). It is seeded with demo data on
> first run. The frontend runs against an in-memory mock by default and can be
> pointed at the real backend. See
> [Frontend → Mock vs. real backend](#mock-vs-real-backend).

## Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic, Uvicorn, **SQLAlchemy 2**
  (with Alembic for migrations). Persistence is a SQL database chosen by the
  `DATABASE_URL` environment variable — SQLite out of the box, Postgres later
  with no code changes. Authentication uses hashed passwords (PBKDF2) and
  opaque bearer tokens (stored in the database, so sessions survive restarts).
- **Frontend:** React 18 + TypeScript, Vite, React Router, Vitest.

## Prerequisites

| Tool | Version | Notes |
| ---- | ------- | ----- |
| [uv](https://docs.astral.sh/uv/) | latest | Python project & dependency manager (`pip install uv`) |
| Python | 3.11+ | Managed by `uv` via `backend/.python-version` |
| Node.js | 18+ | Vite 5 requires Node 18.17+ |
| npm | 9+ | Ships with Node |

## Project layout

```
backend/      # FastAPI application, SQLAlchemy store, migrations, and tests
docs/         # spec.md, process.md, task-template.md, team roles
frontend/     # React + TypeScript SPA
openapi.yaml  # The API contract both sides implement
AGENTS.md     # Instructions for coding agents
```

## Quickstart

The fastest path uses the [`Makefile`](Makefile) — every common task is a
single memorable `make` target. From a clean clone:

```bash
make setup    # install backend (uv) + frontend (npm) dependencies
make run      # start backend (:8000) + frontend (:5173) in real mode; Ctrl+C stops both
```

Open <http://localhost:5173> and sign in with `dba` / `dba-password`. `make run`
starts the frontend in **real mode**, so everything you do in the UI is served
by the FastAPI backend. To run the UI against its built-in mock instead (no
backend needed), use `make mock` — see
[Mock vs. real backend](#mock-vs-real-backend).

Prefer containers? `docker compose up --build` starts the whole product —
the API+SPA container plus a Postgres database it waits for — on
<http://localhost:8080>; see [Compose: app + Postgres](#compose-app--postgres).

With the backend running, the interactive API docs (Swagger UI) are served at
<http://127.0.0.1:8000/docs>.

Run `make` (or `make help`) at any time to list every target. See
[Makefile](#makefile) for the full reference.

<details>
<summary>Prefer raw commands? They are documented per section below.</summary>

Run the two halves in separate terminals. The backend serves the API on
`http://localhost:8000`; the frontend dev server runs on
`http://localhost:5173` and proxies API calls to the backend.

**Terminal 1 — backend:**

```bash
cd backend
uv sync                                   # install dependencies into backend/.venv
uv run uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — frontend:**

```bash
cd frontend
npm install
npm run dev                               # http://localhost:5173
```

</details>

---

## Backend

A FastAPI app implementing [`openapi.yaml`](openapi.yaml). All state lives in a
SQL database (`backend/app/store.py` talks to it through SQLAlchemy) that is
seeded with demo data the first time it is created, so the API is immediately
usable on a clean checkout. Restarting the backend keeps your data — it is no
longer held in process memory.

### Setup

```bash
cd backend
uv sync
```

This creates `backend/.venv` (Python 3.11) and installs `fastapi`, `httpx`,
`uvicorn`, `sqlalchemy`, and `alembic`.

### Database configuration

The database is selected with one environment variable —
`SDIP_DATABASE_URL` (or plain `DATABASE_URL`; SQLAlchemy URL format):

| Value | Database |
| ----- | -------- |
| `sqlite:///./sqlserver_tool.db` | SQLite file next to `backend/` (**default**, used when unset) |
| `sqlite:////abs/path/data.db` | SQLite file at an absolute path |
| `postgresql://user:pass@host:5432/db` | Postgres (any installed driver is auto-pinned; bare `postgresql://` works) |

```bash
# Use a different database for a run
cd backend
DATABASE_URL="sqlite:////tmp/dev.db" uv run uvicorn app.main:app --reload --port 8000
```

#### Postgres locally

SQLite is the zero-config default for development; for a production-like
database, start Postgres in one command and point the backend at it:

```bash
make postgres                                          # Postgres 16 in a container
export SDIP_DATABASE_URL=postgresql://sdip:sdip@localhost:5432/sdip
make run                                               # backend + frontend as usual
```

On first boot the backend creates the schema and seeds the demo data into
the empty database — no migrations to run by hand. `make postgres-down`
removes the container; the `sdip-pgdata` volume keeps your data. The live
integration tests use the same URL when set:

```bash
SDIP_TEST_DATABASE_URL=postgresql://sdip:sdip@localhost:5432/sdip \
  uv run pytest tests/test_postgres_integration.py
```

See [`backend/.env.example`](backend/.env.example). On startup the app creates
the tables if they are missing and seeds demo data **only** when the database
is empty — existing data is never overwritten. Schema changes are managed with
Alembic (see [Migrations](#migrations)).

### CORS

Browsers may only call the API from origins it allows. By default those are
the local Vite dev servers (`http://localhost:5173`, `http://127.0.0.1:5173`)
— exactly what `make run` uses, so local development needs no configuration.
To let a deployed frontend (e.g. Vercel) call the API, list its URL in
`CORS_ALLOW_ORIGINS` (comma-separated for several):

```bash
CORS_ALLOW_ORIGINS="https://my-app.vercel.app" uv run uvicorn app.main:app --port 8000
```

### Live collection

A run started **without** credentials produces the seeded demo report. A
run started **with** credentials collects real evidence from the target
instance through the ODBC Driver 18 (#31):

```bash
curl -X POST http://localhost:8000/investigations/1/run \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"credentials": {"username": "svc_diag", "password": "…"}}'
```

- Only the DBA-approved catalog queries run — read-only, with a per-query
  timeout (`LIVE_QUERY_TIMEOUT_SECONDS`, default 30s).
- SQL auth needs a username and password; `ad` uses integrated/Kerberos
  auth from the host's own identity.
- Credentials are **session-only**: used to open the connection, never
  stored in the investigation, job, report, or logs (spec §6). The
  frontend keeps them in memory only, between the form and the run start.
- Connection or query failures become `unavailable` sources with explicit
  coverage gaps — never fabricated findings (spec §3.8).
- The driver is optional at runtime: without it (or without a reachable
  server) the report shows every source as unavailable, with the reason.
- Real-server integration test: set `SQLDIAG_TEST_SERVER` (plus `_PORT`,
  `_USER`, `_PASSWORD`) and run `uv run pytest tests/test_live.py` — the
  env-gated case connects for real.

The backend Docker image ships the driver, so a deployed instance can
collect live evidence as soon as a server is reachable.

### Run

```bash
uv run uvicorn app.main:app --reload --port 8000
```

- `--reload` restarts on code changes (dev only).
- Drop `--reload` for a single-process run.

> **Interactive API docs:** once the server is running, open
> <http://127.0.0.1:8000/docs> for the auto-generated Swagger UI. You can
> browse every endpoint, read the schemas, and **Try out** calls directly —
> log in via `POST /auth/login` and click **Authorize** to use your bearer
> token on the protected endpoints for the rest of the session. (FastAPI
> also serves a read-only schema at `/openapi.json` and ReDoc at `/redoc`.)

> **Note:** if you see a warning that `VIRTUAL_ENV` does not match the project
> environment, it is harmless — `uv` uses `backend/.venv` regardless. To silence
> it, run `unset VIRTUAL_ENV` first.

### Test and lint

```bash
uv run pytest           # run the test suite (33 tests)
uv run ruff check .     # lint
```

### Seed data

The database is seeded **once, when it is first created** (never on subsequent
restarts, so your data is preserved):

| Kind | Value |
| ---- | ----- |
| Organization | `Acme Corp` (issuer `https://idp.acme.example`) |
| Users | `dba` / `dba-password`, `admin` / `admin-password` |
| Catalog | 8 read-only diagnostic queries |
| Investigation | id `1`, status `completed`, with a ready report |

### Try the API

The quickest way to explore is the interactive docs at
<http://127.0.0.1:8000/docs>. To try it from the shell instead, log in to get a
bearer token, then call a protected endpoint:

```bash
# 1. Log in (returns a bearer token)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"dba","password":"dba-password"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# 2. Session (who am I)
curl -s http://localhost:8000/auth/session -H "Authorization: Bearer $TOKEN"

# 3. List investigations
curl -s http://localhost:8000/investigations -H "Authorization: Bearer $TOKEN"

# 4. Fetch the seeded report and its PDF
curl -s http://localhost:8000/reports/1 -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/reports/1.pdf -H "Authorization: Bearer $TOKEN" -o report.pdf
```

### Authentication

- `POST /auth/login` exchanges a username/password for an opaque bearer token.
  Passwords are stored hashed with PBKDF2-HMAC-SHA256 (salted, 120k iterations).
- Send the token on every protected request as `Authorization: Bearer <token>`.
- `GET /health`, `POST /auth/login`, and `GET /auth/authorize` are
  **unauthenticated**. Every other endpoint returns **401** without a valid
  token.

### API surface

All endpoints are documented in [`openapi.yaml`](openapi.yaml). Summary:

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/auth/login` | no | Get a bearer token |
| GET | `/auth/session` | yes | Current caller (org + actor) |
| GET | `/auth/authorize` | no | OIDC authorize URL (stub) |
| GET | `/health` | no | Liveness + version |
| GET | `/investigations` | yes | List investigations |
| POST | `/investigations` | yes | Create an investigation (returns catalog) |
| GET | `/investigations/{id}` | yes | Get one investigation |
| GET | `/investigations/{id}/approvals` | yes | Per-query approval state |
| POST | `/investigations/{id}/approvals` | yes | Record approve/reject decisions |
| POST | `/investigations/{id}/run` | yes | Start a run behind the approval gate |
| GET | `/jobs/{job_id}` | yes | Job status |
| POST | `/jobs/{job_id}/cancel` | yes | Cancel a queued/running job |
| GET | `/reports/{id}` | yes | Report payload (JSON) |
| GET | `/reports/{id}.pdf` | yes | Report as a downloadable PDF |
| GET | `/orgs` | yes | List organizations |
| POST | `/orgs` | yes | Create an organization |
| DELETE | `/orgs/{id}` | yes | Deprovision an organization |

Status codes follow the spec: **401** unauthenticated, **404** not found,
**409** when the approval gate is unsatisfied or a run has already started,
**422** on validation errors (e.g. an unknown catalog id or an invalid
`lookback_hours`).

Every investigation row (list and detail) names its target SQL Server —
`server_host`, `server_port`, `auth_type`, `databases`, `lookback_hours`,
`created_at` — and the backend logs one `sqldiag.investigations` line
naming the server whenever an investigation is opened or a run starts
(never credentials). An observer can therefore see which instance is
under investigation at any stage, without the frontend (#34).

### Project structure

```
backend/app/
  models.py          # Pydantic request/response schemas (the contract)
  db_models.py       # SQLAlchemy ORM tables (the schema)
  db.py              # Engine/session wiring from $DATABASE_URL
  store.py           # DatabaseStore (the persistence layer) + seed data
  security.py        # Password hashing primitives (no dependencies)
  auth.py            # Bearer-token guard
  pdf.py             # Dependency-free PDF renderer for report downloads
  spa.py             # Optional SPA serving when $FRONTEND_DIST is set (#35)
  main.py            # App factory; configures the DB + seeds per app
  routers/
    auth.py          # /auth/*
    health.py        # /health
    investigations.py# investigations, approvals, run
    jobs.py          # job status + cancel
    reports.py       # report JSON + PDF
    orgs.py          # organizations
backend/alembic/     # Alembic migrations (versions/)
backend/tests/       # pytest suite (one module per resource)
```

### Migrations

Tables are created automatically on startup (`create_all`), and the schema is
stamped as Alembic `head` so the tooling stays in sync. When you change the
models in `db_models.py`, generate and apply a migration:

```bash
make migrate-new MSG="describe the change"   # alembic revision --autogenerate
make migrate                                 # alembic upgrade head
make migrate-down                            # revert the last one
```

A fresh database can also be built purely by Alembic —
`cd backend && uv run alembic upgrade head` — after which the app will seed it
on first start.

---

## Frontend

A React + TypeScript single-page app built with Vite. Every backend call goes
through a single typed service client (`frontend/src/services/`), which has two
interchangeable implementations: an in-memory **mock** and a **real** HTTP
client.

### Setup

```bash
cd frontend
npm install
```

### Run

```bash
npm run dev          # dev server at http://localhost:5173
```

### Test, type-check, and build

```bash
npm test             # run the Vitest suite once
npm run typecheck    # TypeScript type-check only
npm run build        # type-check + production build to frontend/dist
npm run preview      # serve the production build locally
```

### Mock vs. real backend

`make run` starts the frontend in **real mode** (talking to the backend on
`:8000`); `make mock` runs it against the built-in mock with no backend. The
mode is chosen at build time via environment variables (see
`frontend/.env.example`):

| Variable | Effect |
| -------- | ------ |
| `VITE_USE_MOCK` | `true` uses the in-memory mock; `false` (the default under `make run`) uses the real HTTP client |
| `VITE_API_BASE_URL` | Base URL for the real client, no trailing slash (e.g. `http://localhost:8000`). Leave empty to use the Vite proxy (same-origin) |

**Run against the real backend (recommended):**

```bash
make run        # backend + frontend together, real mode
```

**Run against the mock (default for a bare `npm run dev`, no backend needed):**

```bash
make mock       # or: cd frontend && npm run dev
```

With `VITE_API_BASE_URL` empty, the Vite dev server proxies `/health`,
`/auth`, `/investigations`, `/jobs`, `/reports`, and `/orgs` to
`http://localhost:8000` (see `frontend/vite.config.ts`), so no CORS
configuration is needed in dev.

### Signing in (mock and real)

The frontend authenticates with a username/password form on the login
screen. It posts to `POST /auth/login`, stores the returned opaque token
in `localStorage` (`frontend/src/services/token.ts`), and sends it on
every protected request as `Authorization: Bearer <token>`. The session
is resolved via `GET /auth/session`; signing out clears the stored token.

The same seeded credentials work in **both** modes, so you can exercise
the login flow with or without a backend:

| Username | Password |
| -------- | -------- |
| `dba` | `dba-password` |
| `admin` | `admin-password` |

### Verify the full stack (two windows)

With `make run` up, you can confirm the UI and the backend are genuinely
connected:

1. **Window 1** — sign in as `dba`, click **New investigation**, fill the form,
   and submit. You land on the approval screen: approve every query, then
   **Start run**. You're taken to the run status, then **View report**.
2. **Check the backend** — `backend` logs show each `POST`, and
   <http://127.0.0.1:8000/docs> lists the investigation and its report.
3. **Window 2** (a normal or private window) — sign in as `admin`. The hub's
   investigation list shows the investigation window 1 created, and opens the
   same report.

This works because both windows talk to **one shared database** on the backend.
There is no real-time push: window 2 sees new items after a **Refresh** (or
reload). The database persists across backend restarts, so your investigations
and sessions are still there when you bring it back up.

### Project structure

```
frontend/src/
  services/          # Typed service client: types, mock, real HTTP, factory
    serviceClient.ts # The ServiceClient interface (single source of truth)
    mockService.ts   # In-memory implementation (default)
    realService.ts   # HTTP implementation
    http.ts          # Low-level fetch transport (attaches the bearer token)
    token.ts         # Bearer-token storage (localStorage)
    index.ts         # Factory: picks mock vs real from env
  pages/             # Route-level screens
    LoginPage.tsx        # username/password sign-in
    LandingPage.tsx      # hub: session, new investigation, investigation list
    ConnectionScopeForm.tsx  # create an investigation
    ApprovalScreen.tsx   # approve/reject catalog queries, start the run
    JobStatusPage.tsx    # run status (polls until terminal)
    ReportPage.tsx       # report dashboard + PDF download
  components/        # Reusable UI components
    HealthStatus.tsx         # API health indicator
    InvestigationList.tsx    # shared-store investigation list
    QuerySql.tsx             # read-only SQL viewer
  auth/              # Auth context + route guard
  __tests__/         # Vitest tests
```

---

## Makefile

Every common task is wrapped as a `make` target so you don't have to remember
the underlying commands. Run `make` (or `make help`) to list them at any time.

### Everyday commands

| Command | What it does |
| ------- | ------------ |
| `make` / `make help` | List all targets (this is the default target). |
| `make setup` | Install backend (`uv sync`) and frontend (`npm install`) dependencies. |
| `make run` | Start the **whole stack** — backend on `:8000` and frontend on `:5173` in **real mode**. Press Ctrl+C to stop both. |
| `make mock` | Start only the frontend dev server against its in-memory mock (no backend). |
| `make migrate` | Apply database migrations (`alembic upgrade head`). |
| `make migrate-new` | Generate a migration from model changes (`make migrate-new MSG="..."`). |
| `make migrate-down` | Revert the last migration. |
| `make test` | Run the backend (pytest) and frontend (vitest) test suites. |
| `make token` | Print a bearer token for the seeded `dba` user (backend must be running). |
| `make clean` | Remove caches and the frontend build output. |

### Run one half

Two terminals, frontend and backend connected through the dev proxy:

```bash
# terminal 1
make backend

# terminal 2
make frontend   # real mode (VITE_USE_MOCK=false) — talks to terminal 1
```

| Command | What it does |
| ------- | ------------ |
| `make backend` | Run only the backend API on `:8000`. |
| `make frontend` | Run only the frontend dev server on `:5173` in **real mode** (talks to `make backend`; use `make mock` for the mock-only version). |

### Backend

| Command | What it does |
| ------- | ------------ |
| `make backend-install` | Install backend dependencies (`uv sync`). |
| `make backend-test` | Run backend tests (`uv run pytest`). |
| `make backend-lint` | Lint the backend (`uv run ruff check .`). |
| `make backend-format` | Format backend code (`uv run ruff format .`). |

### Frontend

| Command | What it does |
| ------- | ------------ |
| `make frontend-install` | Install frontend dependencies (`npm install`). |
| `make frontend-test` | Run frontend tests (`npm test`). |
| `make frontend-typecheck` | Type-check the frontend (`npm run typecheck`). |
| `make frontend-build` | Type-check and build the frontend for production (`npm run build`). |

### Container

| Command | What it does |
| ------- | ------------ |
| `make image` | Build the single-image container (API + built frontend) as `sqldiag:local`. |
| `make container` | Build it and run it on `:8000` — the backend serves the whole app. |

### How `make run` works

`make run` starts the backend in the background (logging to
`/tmp/sql-server-tool-backend.log`), waits for it to become healthy, then starts
the frontend in the foreground. Ctrl+C tears down both — a shell `trap` kills the
background backend so nothing is left running. Prefer to control them separately?
Use `make backend` and `make frontend` in two terminals instead.

Targets that wrap `uv` or `npm` run them from the correct directory for you, so
you never need to `cd` first.

---

## End-to-end tests

[`e2e/`](e2e/) holds a Playwright suite that exercises the product's
core two-session journey against the **docker-compose stack**: a DBA logs
in and creates an investigation, an approver opens the shared approval
link in a separate browser context (its own session), reviews and
approves every generated query, starts the run — and the DBA sees the
status flip to completed and the report render. It also checks that
anonymous visitors are bounced to the login screen.

```bash
make e2e
```

That single command starts the stack (waiting for the app's healthcheck,
which itself waits for Postgres), builds the `e2e` runner image, and runs
the suite **on the compose network** (`E2E_BASE_URL=http://app:8000`) —
no browsers or Node packages needed on the host.

The runner is version-locked: `e2e/package.json` pins `@playwright/test`
and `e2e/Dockerfile` pins the matching browser image — bump both
together. The suite can also run against any deployment:

```bash
cd e2e && E2E_BASE_URL=https://your-app.onrender.com npx playwright test
```

## Deployment

The frontend deploys to **Vercel** (a static SPA built with Vite); the backend
deploys as a **container** (Render / Fly.io / Azure Container Apps). Keeping
the API in a container rather than in Vercel's serverless functions preserves
the path to live SQL Server diagnosis (#31): a container can install the
Microsoft ODBC Driver 18 and keep state, a function cannot.

Two shapes: the **single container** below (one service, one origin, the
smallest footprint — recommended for Render's free tier), or the **split
deploy** (Vercel frontend + API container) if you want them independent.

### One container (API serves the frontend)

The root [`Dockerfile`](Dockerfile) is multi-stage: Node builds the React
bundle, then the API image receives it and serves it at `/` behind the API
routes (`FRONTEND_DIST` → [`backend/app/spa.py`](backend/app/spa.py)):

```bash
docker build -t sdip:latest .

docker run --rm -p 8080:8000 \
  -v sdip-data:/data \
  -e SDIP_DATABASE_URL=sqlite:////data/sdip.db \
  --name sdip sdip:latest
# → http://localhost:8080 serves both the SPA and the API
```

Or via make: `make image` (build) and `make container` (build + run).

- The named volume `sdip-data` keeps the SQLite database between
  container runs; without it, demo data reseeds on every start.
- `SDIP_DATABASE_URL` is the product-prefixed database setting (plain
  `DATABASE_URL` also works); point either at Postgres for #33 — the
  driver is already in the image.

- The bundle is baked with `VITE_USE_MOCK=false` and an empty
  `VITE_API_BASE_URL`, so every call is same-origin — no CORS config at all.
- Serving is opt-in: set `FRONTEND_DIST` to a directory of built assets and
  the app mounts it; leave it unset and the app stays API-only, exactly as
  `make run`, the tests, and the split deploy expect. Unknown extension-less
  paths fall back to `index.html` for React Router; real files keep honest
  404s. API routes, `/docs`, and `/openapi.json` always win over the SPA.
- Persistence: the default SQLite file lives inside the container. Attach a
  volume and point `SDIP_DATABASE_URL` at it (the `make container`
  target does this with the `sdip-data` volume), or use the Compose
  stack below, which runs the app against Postgres.
- Deep links and hard refreshes work on every screen, including the
  paths that collide with API routes (`/investigations`,
  `/investigations/new`, `/jobs/<id>`): browser page loads carry no bearer
  token, so those get `index.html`, while API clients (fetch/XHR) keep
  their exact 401/JSON semantics — see `app/spa.py`.
- On Render: New → Web Service → Docker, Root Directory left at the repo
  root, and this single service runs everything.

### Compose: app + Postgres

[`docker-compose.yaml`](docker-compose.yaml) runs the same single image
**together with a Postgres database** — the production-shaped local stack:

```bash
docker compose up --build
# → http://localhost:8080 serves the SPA + API, backed by Postgres
```

- The `db` service (Postgres 16) carries a healthcheck; the `app` service
  waits for it to report **healthy** (`depends_on: condition:
  service_healthy`) before starting, so the backend never races an
  initializing Postgres — it finds a ready database, creates the schema,
  and seeds the demo data on first boot.
- Postgres data lives in the Compose-managed `pgdata` volume; the database
  port is not published (the app reaches it as `db:5432` over the Compose
  network). Inspect it with `docker compose exec db psql -U sdip`.
- `docker compose down` stops the stack and keeps the data;
  `docker compose down -v` also drops the volume (fresh reseed next time).
- The standalone `make container` target also uses host port 8080 — remove
  that container first (`docker rm -f sdip`; its `sdip-data` volume is
  untouched) before `docker compose up`.
- If the app ever crash-loops with `failed to resolve host 'db'`, Docker
  Desktop (WSL2 backend) has dropped the container's network attachment —
  the compose file is fine. Recreate it:
  `docker compose up -d --force-recreate app`.

### Deploy to Render (blueprint)

[`render.yaml`](render.yaml) describes the production shape: one web
service (the single image — API serves the SPA) plus a free managed
Postgres, wired together via `SDIP_DATABASE_URL`.

1. Push the repository to GitHub (Render reads the blueprint from it).
2. Render Dashboard → **New → Blueprint** → select the repository.
   Render builds the root `Dockerfile`, provisions `sdip-db`, and
   deploys the service.
3. The app is live at **`https://sql-server-n68y.onrender.com`** —
   Render suffixes the service name when the plain one is taken
   globally (names are global; the CI variable `RENDER_APP_URL` must
   match the final URL shown on the service page).

CI/CD ([`.github/workflows/ci.yaml`](.github/workflows/ci.yaml)): every
push runs backend + frontend tests **in parallel**, then builds the
compose stack and runs the Playwright e2e suite against it, and — on
`main` only — triggers the Render deploy and **waits for `/health` to
return 200** before reporting success. To enable deploys, add the
service's **Settings → Deploy hooks** URL as the `RENDER_DEPLOY_HOOK`
repository secret; until then the deploy job no-ops with a message.

> The original pipeline brief targeted AWS with a GitHub OIDC role;
> the owner swapped the destination to Render, where a deploy-hook
> POST replaces the cloud role.

Free-plan notes: the service sleeps after inactivity (first visit waits
~30-60s), and free Postgres instances recycle after 90 days — the demo
data reseeds automatically. Point `E2E_BASE_URL` at the deployed URL to
run the e2e journey against production too.

### Frontend on Vercel

1. Import the GitHub repository at vercel.com/new — the **Vite** framework
   preset is auto-detected.
2. Set **Root Directory** to `frontend` (Settings → General); this repo is a
   monorepo.
3. Under Settings → Environment Variables, add what the SPA should call:
   - `VITE_API_BASE_URL` = `https://your-api.example.com` (no trailing slash).
   - `VITE_USE_MOCK` unset (`false`) calls the real API; set to `true` for a
     self-contained demo using the bundled mock data.
4. Deploy. [`frontend/vercel.json`](frontend/vercel.json) already contains the
   SPA rewrite, so React Router deep links (`/investigations/1/report`) work
   on a page refresh.

`make run` and plain `npm run dev` are unaffected: with `VITE_API_BASE_URL`
empty the client calls same-origin paths, and the Vite dev proxy forwards
them to `http://localhost:8000` exactly as before.

### Backend as a container

[`backend/Dockerfile`](backend/Dockerfile) builds the API with the locked `uv`
environment, listens on `$PORT` (every platform above sets one), and runs as a
non-root user:

```bash
# Local smoke test
cd backend
docker build -t sql-server-tool-api .
docker run --rm -p 8000:8000 -e CORS_ALLOW_ORIGINS=http://localhost:5173 sql-server-tool-api
```

Platform setup (each supports a subdirectory as the project root):

| Platform | Setting |
| -------- | ------- |
| Render | New → Web Service → Docker, Root Directory `backend` |
| Fly.io | `fly launch` from `backend/` (build context = this directory) |
| Azure Container Apps | Build from `backend/Dockerfile` in the repo |

Environment variables for the service:

| Variable | Purpose |
| -------- | ------- |
| `CORS_ALLOW_ORIGINS` | Your Vercel URL(s), comma-separated — required so the deployed SPA can call the API |
| `DATABASE_URL` | Defaults to the container's SQLite file; attach a persistent disk or point at managed Postgres to survive restarts |

Finally, set the same API URL as `VITE_API_BASE_URL` on Vercel and redeploy
the frontend.

> **Note:** the container's filesystem is ephemeral on most platforms, so the
> SQLite database (logins, investigations) resets on each deploy or restart
> unless you attach a disk or use Postgres. Demo data reseeds automatically
> whenever the database is empty.

---

## Documentation

- [`openapi.yaml`](openapi.yaml) — the API contract both backend and frontend implement.
- [`docs/spec.md`](docs/spec.md) — product scope (POC).
- [`docs/process.md`](docs/process.md) — how work is organized (roles and lifecycle).
- [`docs/task-template.md`](docs/task-template.md) — the template used for backlog items.
- [`docs/team/`](docs/team/) — agent roles (PM, engineer, QA).
- [`docs/ad-auth-spike.md`](docs/ad-auth-spike.md) — notes on Active Directory auth.

Tasks are tracked as GitHub issues in this repository.

## Troubleshooting

| Symptom | Fix |
| ------- | --- |
| Want to explore the API interactively | Open the Swagger UI at <http://127.0.0.1:8000/docs> while the backend runs. |
| `ModuleNotFoundError: No module named 'fastapi'` when running uvicorn | Run `uv sync` from `backend/`, and make sure you invoke via `uv run uvicorn ...` so the project venv is used. |
| Warning that `VIRTUAL_ENV` does not match the project environment | Harmless; `uv` uses `backend/.venv`. Run `unset VIRTUAL_ENV` to silence it. |
| `401 Unauthorized` from protected endpoints | Include the token: `-H "Authorization: Bearer $TOKEN"`. Get one via `POST /auth/login`. |
| Frontend shows mock data even though the backend runs | Expected — the mock is the default. Set `VITE_USE_MOCK=false` in `frontend/.env.local`. |
| Frontend cannot reach the backend in real mode | Keep the Vite proxy config (leave `VITE_API_BASE_URL` empty) or set it to `http://localhost:8000` and ensure the backend is running. |
| Want to reset all data back to the demo seed | Stop the backend and delete the database file: `rm -f backend/sqlserver_tool.db` (or the path in `DATABASE_URL`). It re-seeds on the next start. |
| `docker rm -f $(docker ps -aq)` says "requires at least 1 argument" | Harmless — it means **no containers exist** (the list was empty, so the command got no arguments). Your Docker is already clean. |

### Docker cleanup cheat-sheet

```bash
# Empty-safe "remove every container" (quiet when there are none):
docker ps -aq | xargs -r docker rm -f

# The same thing the hard way — note the gotcha:
docker rm -f $(docker ps -aq)   # errors "requires at least 1 argument" when no containers exist

# Compose stack lifecycle:
docker compose down        # stop app + db, KEEP the pgdata volume (data survives)
docker compose down -v     # stop and DELETE the volume too (fresh schema + reseed next up)

# Tidy images and build cache:
docker system prune -f     # dangling images, stopped containers, unused build cache
```
