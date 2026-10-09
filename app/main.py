"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app import __version__
from app.config import Settings, load_settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="SQL Server Diagnostic Assistant", version=__version__)
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


settings = load_settings()
app = create_app(settings)