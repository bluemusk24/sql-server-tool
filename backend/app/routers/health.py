"""Health router: unauthenticated liveness probe."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.models import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus(status="ok", version=__version__)
