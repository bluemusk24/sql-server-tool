# syntax=docker/dockerfile:1
# SQL Server Diagnostic Assistant — single image, whole product (#35).
#
# Build from the repository root:
#   docker build -t sqldiag:local .
#   docker run --rm -p 8000:8000 sqldiag:local
# then open http://localhost:8000 — FastAPI serves both the API and the
# built SPA (same origin), so one container runs everything. That is the
# shape Render's free web service wants (see render.yaml, #33).
#
# Stage 1 builds the frontend with Node; stage 2 is the API image from
# backend/Dockerfile with the static bundle dropped in. Keep the runtime
# stage in sync with backend/Dockerfile (the API-only image for split
# deploys such as the Vercel frontend + backend pair).

# ── Stage 1: frontend build ─────────────────────────────────────────
FROM node:22-alpine AS frontend-build

WORKDIR /src/frontend

# Dependency layer first — cached until the lockfile changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# VITE_USE_MOCK=false bakes the real service client into the bundle
# (mock is the default), and an empty VITE_API_BASE_URL makes every call
# same-origin — which lands on this container's own API.
ENV VITE_USE_MOCK=false \
    VITE_API_BASE_URL=

RUN npm run build

# ── Stage 2: API + static bundle ────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv ships its own binary image; copy the CLI in.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# ODBC Driver 18 for SQL Server — live collection (#31) talks to real
# instances through this; unixODBC backs pyodbc and the GSSAPI library
# enables Active Directory integrated auth.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg unixodbc libgssapi-krb5-3 \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first — cached until pyproject.toml/uv.lock change.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# Application code. Alembic migrations ride along; startup stamps the
# baseline via init_schema (see app/db.py).
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

# The built SPA. $FRONTEND_DIST makes the API mount it as its last
# route (see app/spa.py); unset would keep this an API-only image.
COPY --from=frontend-build /src/frontend/dist /app/frontend
ENV FRONTEND_DIST=/app/frontend

# Non-root, and owns /app so the default SQLite file can be created
# there. Mount a volume at /data and set
# DATABASE_URL=sqlite:////data/sqlserver_tool.db for persistence, or
# point DATABASE_URL at a Postgres instance (#33).
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /health is the liveness endpoint; start-period covers the slow first
# boot on mounted filesystems.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

# --no-sync: the environment is already frozen in the image; no network at boot.
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
