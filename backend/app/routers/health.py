"""Health router: unauthenticated liveness probe."""

from __future__ import annotations

import os

from fastapi import APIRouter

from app import __version__
from app.models import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    # #38: CI polls until /health reports the commit it deployed, so
    # "deploy succeeded" means the new code is actually serving traffic.
    # #42: registry images bake SDIP_IMAGE/SDIP_BUILD_SHA — the baked SHA
    # wins because image-backed services have no git link (Render's
    # RENDER_GIT_COMMIT, when present at all, describes the repo tip,
    # not the image that is running).
    return HealthStatus(
        status="ok",
        version=__version__,
        commit=(
            os.getenv("SDIP_BUILD_SHA")
            or os.getenv("RENDER_GIT_COMMIT")
            or None
        ),
        image=os.getenv("SDIP_IMAGE") or None,
    )
