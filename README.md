# SQL Server Diagnostic Assistant

An on-demand, **read-only** assistant that helps DBAs diagnose SQL Server and
application performance incidents. A DBA signs in, enters a SQL Server
connection, reviews and approves the diagnostic query batch, and receives
evidence, likely causes, validation checks, and remediation guidance.

The product never runs remediation and never stores credentials — it only
collects read-only evidence and recommends next steps.

> **Current state:** the backend is an in-memory FastAPI service (seeded with
> demo data, no database yet) that implements the API contract in
> [`openapi.yaml`](openapi.yaml). The frontend runs against an in-memory mock
> by default and can be pointed at the real backend. See
> [Frontend → Mock vs. real backend](#mock-vs-real-backend).

## Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic, Uvicorn. Persistence is an
  in-memory store for now (a real database is the intended next step).
  Authentication uses hashed passwords (PBKDF2) and opaque bearer tokens.
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
backend/      # FastAPI application, in-memory store, and tests
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

A FastAPI app implementing [`openapi.yaml`](openapi.yaml). All state lives in
an in-memory store (`backend/app/store.py`) that is seeded with demo data on
startup, so the API is immediately usable without a database.

### Setup

```bash
cd backend
uv sync
```

This creates `backend/.venv` (Python 3.11) and installs `fastapi`, `httpx`,
and `uvicorn`.

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

The store is seeded on startup so you have data to explore:

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

### Project structure

```
backend/app/
  models.py          # Pydantic request/response schemas (the contract)
  store.py           # In-memory store + seed data (swappable persistence layer)
  auth.py            # Password hashing + bearer-token guard
  pdf.py             # Dependency-free PDF renderer for report downloads
  main.py            # App factory; attaches a fresh store per app
  routers/
    auth.py          # /auth/*
    health.py        # /health
    investigations.py# investigations, approvals, run
    jobs.py          # job status + cancel
    reports.py       # report JSON + PDF
    orgs.py          # organizations
backend/tests/       # pytest suite (one module per resource)
```

### Swapping in a real database

Persistence is isolated behind `InMemoryStore` in `backend/app/store.py`. To
move to a real database, replace that module with a database-backed
implementation of the same methods — the routers and the HTTP contract do not
change. The in-memory store keeps the frontend–backend integration stable while
the persistence layer evolves.

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

This works because both windows talk to **one shared in-memory store** on the
backend. There is no real-time push: window 2 sees new items after a
**Refresh** (or reload). The store is in-memory, so it resets whenever the
backend restarts.

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
| `make test` | Run the backend (pytest) and frontend (vitest) test suites. |
| `make token` | Print a bearer token for the seeded `dba` user (backend must be running). |
| `make clean` | Remove caches and the frontend build output. |

### Run one half

| Command | What it does |
| ------- | ------------ |
| `make backend` | Run only the backend API on `:8000`. |
| `make frontend` | Run only the frontend dev server on `:5173`. |

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

### How `make run` works

`make run` starts the backend in the background (logging to
`/tmp/sql-server-tool-backend.log`), waits for it to become healthy, then starts
the frontend in the foreground. Ctrl+C tears down both — a shell `trap` kills the
background backend so nothing is left running. Prefer to control them separately?
Use `make backend` and `make frontend` in two terminals instead.

Targets that wrap `uv` or `npm` run them from the correct directory for you, so
you never need to `cd` first.

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
