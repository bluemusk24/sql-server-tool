"""FastAPI application factory and module-level ASGI app.

Wires the routers, CORS (so browsers on allowed origins — the local Vite
dev servers by default, plus any origins listed in ``$CORS_ALLOW_ORIGINS``,
e.g. a frontend deployed on Vercel — can call the API), and configures the
database from ``$SDIP_DATABASE_URL`` (or ``$DATABASE_URL``; SQLite by
default). Tests build a fresh app via ``create_app(database_url=...)`` so
each gets its own isolated database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.db import (
    get_database_url,
    init_schema,
    make_engine,
    make_session_factory,
    normalize_database_url,
)
from app.mcp_server import (
    _CanonicalMcpPath,
    mcp_http_app,
)
from app.mcp_server import (
    server as mcp_server,
)
from app.routers import auth, fleet, health, investigations, jobs, orgs, reports
from app.spa import mount_frontend
from app.store import apply_seed_overrides, seed_database
from app.telemetry import setup_telemetry

# Origins permitted when $CORS_ALLOW_ORIGINS is unset: the Vite dev server
# that `make run` starts, so local development needs no configuration.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def allowed_origins() -> list[str]:
    """Browser origins permitted by CORS.

    ``$CORS_ALLOW_ORIGINS`` is a comma-separated list — typically the
    deployed frontend's URL (e.g. ``https://my-app.vercel.app``). When it is
    unset or blank, the local Vite dev servers are used so `make run` keeps
    working with no extra configuration.
    """
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or DEV_ORIGINS


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Drive lifecycles that must run inside the event loop.

    Seeding and engine setup stay in ``create_app`` (they run at factory
    time). The MCP server's streamable-HTTP session manager (#51) is the
    exception: mounted apps never receive lifespan events, so its task
    group is started and stopped here.
    """
    async with mcp_server.session_manager.run():
        yield


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(
        title="SQL Server Diagnostic Assistant",
        version=__version__,
        lifespan=_lifespan,
    )

    # Allow approved origins (see `allowed_origins`) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Database: URL comes from the argument, else $DATABASE_URL, else SQLite.
    # Postgres URLs get pinned to the installed driver either way.
    url = (
        normalize_database_url(database_url)
        if database_url is not None
        else get_database_url()
    )
    engine = make_engine(url)
    app.state.database_url = url
    app.state.engine = engine
    app.state.SessionLocal = make_session_factory(engine)
    init_schema(engine, url)

    # OpenTelemetry (#44): auto-instrument requests and queries when an
    # OTLP endpoint is configured — a no-op otherwise.
    setup_telemetry(app)

    # Seed demo data once, on a fresh database; then rotate any seeded
    # demo login to its SEED_*_PASSWORD override (#41).
    session = app.state.SessionLocal()
    try:
        seed_database(session)
        apply_seed_overrides(session)
        session.commit()
    finally:
        session.close()

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(orgs.router)
    app.include_router(investigations.router)
    app.include_router(jobs.router)
    app.include_router(reports.router)
    app.include_router(fleet.router)

    # MCP server (#51): /mcp beside the API, bearer-guarded and reading
    # the same store as the routes above. The middleware canonicalizes
    # the bare /mcp path so no redirect stands between an MCP client and
    # authentication; mounted before the SPA so the catch-all never
    # sees it; stateless so each POST is self-contained.
    app.add_middleware(_CanonicalMcpPath)
    app.mount("/mcp", mcp_http_app())

    # The built SPA, when the image ships one ($FRONTEND_DIST). Mounted
    # last so every API route above — and /docs — keeps priority (#35).
    mount_frontend(app)
    return app


app = create_app()
