"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app import __version__
from app.config import Settings, load_settings
from app.logging_setup import configure_logging


def create_app(settings: Settings) -> FastAPI:
    configure_logging()
    app = FastAPI(title="SQL Server Diagnostic Assistant", version=__version__)
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


settings = load_settings()
app = create_app(settings)