"""FastAPI application entrypoint."""

from typing import Any

from fastapi import FastAPI, HTTPException

from app import __version__
from app.config import Settings, load_settings
from app.diagnostics.catalog import load_catalog
from app.logging_setup import configure_logging


def create_app(settings: Settings) -> FastAPI:
    configure_logging()
    app = FastAPI(title="SQL Server Diagnostic Assistant", version=__version__)
    app.state.settings = settings

    # Explicit, fail-fast load of the read-only catalog (no import-time I/O).
    catalog = load_catalog()
    app.state.catalog = catalog

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/catalog")
    def list_catalog() -> list[dict[str, Any]]:
        """The full ordered catalog for DBA review, SQL text echoed verbatim."""
        return [entry.to_dict() for entry in catalog.all()]

    @app.get("/catalog/{query_id}")
    def get_catalog_entry(query_id: str) -> dict[str, Any]:
        """One catalog entry by id, or 404 when it is not in the catalog."""
        entry = catalog.get(query_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="catalog query not found")
        return entry.to_dict()

    return app


settings = load_settings()
app = create_app(settings)