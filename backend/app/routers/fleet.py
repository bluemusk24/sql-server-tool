"""Fleet router: org-scoped failure summary across the firm's servers (#49).

The multi-firm analysis view: one endpoint that answers "which of *our*
servers are failing, in which areas, and is the refresh path among
them?" for the caller's organization only (spec §6 — platform
operators never see cross-organization data).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.store import DatabaseStore, get_store

router = APIRouter(tags=["fleet"])


@router.get("/fleet")
def get_fleet(
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Failure summary across every server this organization has
    investigated: findings by severity, per-area breakdown (refresh
    failures highlighted), and the latest primary cause per server."""
    return store.fleet_summary(user.organization)
