"""FastAPI application factory and module-level ASGI app.

Wires the routers, CORS (so the Vite dev server on :5173 can call the API),
and attaches the in-memory store to ``app.state``. Tests build a fresh app
via ``create_app()`` so each gets its own store.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.routers import auth, health, investigations, jobs, orgs, reports
from app.store import InMemoryStore


def create_app(store: InMemoryStore | None = None) -> FastAPI:
    app = FastAPI(title="SQL Server Diagnostic Assistant", version=__version__)

    # Allow the Vite dev server to call the API with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.store = store if store is not None else InMemoryStore()

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(investigations.router)
    app.include_router(jobs.router)
    app.include_router(reports.router)
    app.include_router(orgs.router)

    return app


app = create_app()
