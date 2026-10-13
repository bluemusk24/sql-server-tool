# SQL Server Diagnostic Assistant — API image (#32).
#
# Build context is this directory (backend/). Suitable for Render, Fly.io,
# or Azure Container Apps: honours $PORT, runs as a non-root user, and keeps
# the locked uv environment. pyodbc and the Microsoft ODBC Driver 18 are
# intentionally absent until the live-connection work in #31 lands.

# Pinned to bookworm: trixie renamed libgssapi-krb5-3 (→ -2), and the
# Microsoft repo below targets Debian 12 anyway.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# uv ships its own binary image; copy the CLI in.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# ODBC Driver 18 for SQL Server — live collection (#31) talks to real
# instances through this. Installed now so the deployed image is ready
# the day a reachable server exists; unixODBC backs pyodbc and the GSSAPI
# library enables Active Directory integrated auth.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg unixodbc libgssapi-krb5-2 \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first — cached until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code. Alembic migrations ride along; startup stamps the
# baseline via init_schema (see app/db.py).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Non-root, and owns /app so the default SQLite file can be created there.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --no-sync: the environment is already frozen in the image; no network at boot.
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
