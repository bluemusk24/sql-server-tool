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

Open <http://localhost:5173>. By default the frontend uses its built-in mock
and needs no backend; to exercise the real backend see
[Mock vs. real backend](#mock-vs-real-backend).

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

Log in to get a bearer token, then call a protected endpoint:

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

Interactive API docs (Swagger UI) are at <http://localhost:8000/docs> once the
server is running.

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

The service client is chosen at build time via environment variables
(see `frontend/.env.example`):

| Variable | Effect |
| -------- | ------ |
| `VITE_USE_MOCK` | `true` (default) uses the in-memory mock; `false` uses the real HTTP client |
| `VITE_API_BASE_URL` | Base URL for the real client, no trailing slash (e.g. `http://localhost:8000`). Leave empty to use the Vite proxy (same-origin) |

**Run against the mock (default, no backend needed):**

```bash
npm run dev
```

**Run against the real backend:**

```bash
# from frontend/, with the backend already running on :8000
echo "VITE_USE_MOCK=false" > .env.local
npm run dev
```

With `VITE_API_BASE_URL` empty, the Vite dev server proxies `/health`,
`/auth`, `/investigations`, `/jobs`, and `/reports` to
`http://localhost:8000` (see `frontend/vite.config.ts`), so no CORS
configuration is needed in dev.

> **Wiring note:** the frontend's HTTP transport currently forwards session
> cookies (`credentials: "include"`) and does not yet attach the
> `Authorization: Bearer <token>` header that the backend requires. To connect
> it to the real backend, `frontend/src/services/http.ts` must store the token
> from `POST /auth/login` and send it on each request. Until then, the frontend
> is best used against its mock.

### Project structure

```
frontend/src/
  services/          # Typed service client: types, mock, real HTTP, factory
    serviceClient.ts # The ServiceClient interface (single source of truth)
    mockService.ts   # In-memory implementation (default)
    realService.ts   # HTTP implementation
    http.ts          # Low-level fetch transport
    index.ts         # Factory: picks mock vs real from env
  pages/             # Route-level screens
  components/        # Reusable UI components
  auth/              # Auth context + route guard
  __tests__/         # Vitest tests
```

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
| `ModuleNotFoundError: No module named 'fastapi'` when running uvicorn | Run `uv sync` from `backend/`, and make sure you invoke via `uv run uvicorn ...` so the project venv is used. |
| Warning that `VIRTUAL_ENV` does not match the project environment | Harmless; `uv` uses `backend/.venv`. Run `unset VIRTUAL_ENV` to silence it. |
| `401 Unauthorized` from protected endpoints | Include the token: `-H "Authorization: Bearer $TOKEN"`. Get one via `POST /auth/login`. |
| Frontend shows mock data even though the backend runs | Expected — the mock is the default. Set `VITE_USE_MOCK=false` in `frontend/.env.local`. |
| Frontend cannot reach the backend in real mode | Keep the Vite proxy config (leave `VITE_API_BASE_URL` empty) or set it to `http://localhost:8000` and ensure the backend is running. |
