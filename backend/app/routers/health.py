"""Health router: unauthenticated liveness probe."""

from __future__ import annotations

import os

from fastapi import APIRouter

from app import __version__
from app.models import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    # #38: Render exports RENDER_GIT_COMMIT for the running instance;
    # CI polls until this matches the commit it pushed, so "deploy
    # succeeded" means the new code is actually serving traffic.
    return HealthStatus(
        status="ok",
        version=__version__,
        commit=os.getenv("RENDER_GIT_COMMIT") or None,
    )
