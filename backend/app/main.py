"""FastAPI application factory and module-level ASGI app.

Wires the routers, CORS (so the Vite dev server on :5173 can call the API),
and configures the database from ``DATABASE_URL`` (SQLite by default). Tests
build a fresh app via ``create_app(database_url=...)`` so each gets its own
isolated database.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.db import get_database_url, init_schema, make_engine, make_session_factory
from app.routers import auth, health, investigations, jobs, orgs, reports
from app.store import seed_database


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="SQL Server Diagnostic Assistant", version=__version__)

    # Allow the Vite dev server to call the API with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Database: URL comes from the argument, else $DATABASE_URL, else SQLite.
    url = database_url if database_url is not None else get_database_url()
    engine = make_engine(url)
    app.state.database_url = url
    app.state.engine = engine
    app.state.SessionLocal = make_session_factory(engine)
    init_schema(engine, url)

    # Seed demo data once, on a fresh database.
    session = app.state.SessionLocal()
    try:
        seed_database(session)
        session.commit()
    finally:
        session.close()

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(orgs.router)
    app.include_router(investigations.router)
    app.include_router(jobs.router)
    app.include_router(reports.router)
    return app


app = create_app()
