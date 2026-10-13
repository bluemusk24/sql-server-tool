"""Serve the built React frontend from the API process (#35).

When ``$FRONTEND_DIST`` points at a directory of static assets — the
single-image Dockerfile builds the SPA in its Node stage and copies the
bundle in — the app serves it with two mechanisms, and everything is
opt-in: unset variable or missing directory means the app stays API-only,
which is what ``make run``, the split Vercel deploy, and the tests expect.

1. **Document fallback** (middleware, before routing): a browser page
   load never carries the bearer token — it lives in localStorage and is
   attached to fetch calls — so SPA routes that *collide* with API route
   patterns (``/investigations``, ``/investigations/new``,
   ``/jobs/<id>``) would be swallowed by the API's auth guard and answer
   401 instead of rendering. Requests that look like browser document
   navigations (``Accept: text/html``, no ``Authorization`` header) on
   those paths get ``index.html`` directly. API calls are untouched.

2. **Static mount** (registered last, after every API route): serves the
   bundle itself and falls back to ``index.html`` for unknown,
   extension-less paths so React Router can route them (deep links,
   hard refreshes). ``/docs``, ``/openapi.json``, and every API path
   keep priority; missing *files* keep their honest 404.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger("sqldiag.spa")

# SPA paths that collide with API route *patterns*. Mirrors the client
# router in frontend/src/App.tsx — change both together.
COLLIDING_SPA_PATH = re.compile(
    r"^/(investigations|investigations/new|jobs/[^/]+)/?$"
)


class SpaStaticFiles(StaticFiles):
    """StaticFiles that serves ``index.html`` for unknown, extension-less
    paths so the SPA can route them (deep links, hard refreshes).
    Requests for missing *files* — paths with an extension, e.g. a
    typo'd asset — keep their honest 404."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            # Starlette raises rather than returns when nothing matches.
            if exc.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not Path(path).suffix:
            return await super().get_response("index.html", scope)
        return response


def _install_document_fallback(app: FastAPI, dist: Path) -> None:
    """Answer browser document navigations to API-colliding SPA paths
    with ``index.html`` (see the module docstring, point 1)."""
    index = dist / "index.html"

    @app.middleware("http")
    async def spa_document_fallback(request: Request, call_next):
        document_navigation = (
            request.method in ("GET", "HEAD")
            and "text/html" in request.headers.get("accept", "")
            and not request.headers.get("authorization")
        )
        if document_navigation and COLLIDING_SPA_PATH.match(request.url.path):
            return FileResponse(index, media_type="text/html")
        return await call_next(request)


def mount_frontend(app: FastAPI) -> None:
    """Serve the SPA when ``$FRONTEND_DIST`` names a real directory;
    otherwise leave the app API-only."""
    dist = os.environ.get("FRONTEND_DIST", "")
    if not dist or not Path(dist).is_dir():
        return
    _install_document_fallback(app, Path(dist))
    # Registered after all routers (see app.main.create_app), so
    # /health, /docs, /openapi.json, … are matched before this mount.
    app.mount("/", SpaStaticFiles(directory=dist, html=True), name="frontend")
    logger.info("serving frontend bundle from %s", dist)


__all__ = ["SpaStaticFiles", "mount_frontend"]
