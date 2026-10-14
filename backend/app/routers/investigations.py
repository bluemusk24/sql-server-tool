"""Investigations router: create/list/get, approvals, and run gating.

All endpoints here require an authenticated session (bearer token). The
approval gate enforces the spec's 409 semantics: decisions cannot change
once a run has started, and a run cannot start until the gate is approved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app import live
from app.auth import CurrentUser, get_current_user
from app.models import (
    ApprovalQuery,
    ApprovalRequest,
    ApprovalResponse,
    CatalogEntry,
    CreateInvestigationRequest,
    InvestigationResponse,
    InvestigationSummary,
    RunAccepted,
    StartRunRequest,
)
from app.store import ApprovalDecision, DatabaseStore, Investigation, get_store

router = APIRouter(tags=["investigations"])

# Backend-side visibility (#34): uvicorn's access log shows paths only, so
# these lines record *which* server each investigation targets. They name
# the host/port/auth — never credentials.
logger = logging.getLogger("sqldiag.investigations")


def _require_inv(store: DatabaseStore, inv_id: int) -> Investigation:
    inv = store.get_investigation(inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return inv


def _inv_target(inv: Investigation) -> dict[str, Any]:
    """The target-server fields shared by the list and detail models."""
    return {
        "server_host": inv.server_host,
        "server_port": inv.server_port,
        "auth_type": inv.auth_type,
        "databases": inv.databases,
        "lookback_hours": inv.lookback_hours,
        "created_at": inv.created_at,
    }


def _approval_state(decisions: dict[str, ApprovalDecision]) -> str:
    """Compute the gate state from per-query decisions."""
    if not decisions:
        return "pending"
    if any(d.decision == "rejected" for d in decisions.values()):
        return "rejected"
    # Approved only if every catalog entry is approved.
    from app.store import CATALOG

    if all(entry.id in decisions for entry in CATALOG) and all(
        d.decision == "approved" for d in decisions.values()
    ):
        return "approved"
    return "pending"


def _approval_response(inv_id: int, decisions: dict[str, ApprovalDecision]) -> ApprovalResponse:
    queries = [
        ApprovalQuery(
            catalog_id=d.catalog_id,
            decision=d.decision,  # type: ignore[arg-type]
            actor=d.actor,
            note=d.note,
            created_at=d.created_at,
        )
        for d in sorted(decisions.values(), key=lambda d: d.catalog_id)
    ]
    return ApprovalResponse(
        investigation_id=inv_id,
        state=_approval_state(decisions),  # type: ignore[arg-type]
        queries=queries,
    )


@router.get("/investigations", response_model=list[InvestigationSummary])
def list_investigations(
    store: DatabaseStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> list[InvestigationSummary]:
    return [
        InvestigationSummary(**row) for row in store.list_investigations()
    ]


@router.post(
    "/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_investigation(
    payload: CreateInvestigationRequest,
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> InvestigationResponse:
    inv = store.create_investigation(
        server_host=payload.server_host,
        server_port=payload.server_port,
        auth_type=payload.auth_type.value,
        databases=payload.databases,
        lookback_hours=payload.lookback_hours,
    )
    logger.info(
        "investigation %s opened for %s:%s (auth=%s, lookback=%sh, scope=%s) by %s",
        inv.id,
        inv.server_host,
        inv.server_port,
        inv.auth_type,
        inv.lookback_hours,
        "all databases" if inv.databases is None else ", ".join(inv.databases),
        user.actor,
    )
    return InvestigationResponse(
        id=inv.id, status=inv.status, catalog=list(inv.catalog), **_inv_target(inv)
    )


@router.get("/investigations/{inv_id}", response_model=InvestigationResponse)
def get_investigation(
    inv_id: int,
    store: DatabaseStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> InvestigationResponse:
    inv = _require_inv(store, inv_id)
    return InvestigationResponse(
        id=inv.id, status=inv.status, catalog=list(inv.catalog), **_inv_target(inv)
    )


@router.delete(
    "/investigations/{inv_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_investigation(
    inv_id: int,
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    _require_inv(store, inv_id)
    if store.investigation_has_active_job(inv_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A run is in progress for this investigation",
        )
    store.delete_investigation(inv_id)
    logger.info("investigation %s deleted by %s", inv_id, user.actor)


@router.get("/investigations/{inv_id}/approvals", response_model=ApprovalResponse)
def get_approvals(
    inv_id: int,
    store: DatabaseStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> ApprovalResponse:
    _require_inv(store, inv_id)
    decisions = store.get_approvals(inv_id) or {}
    return _approval_response(inv_id, decisions)


@router.post("/investigations/{inv_id}/approvals", response_model=ApprovalResponse)
def record_decisions(
    inv_id: int,
    payload: ApprovalRequest,
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> ApprovalResponse:
    inv = _require_inv(store, inv_id)
    if inv.status not in ("draft", "pending_approval"):
        # Run already started (or finished): read-only.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run already started; decisions are locked",
        )

    catalog_ids = {entry.id for entry in inv.catalog}
    unknown = [cid for cid in payload.decisions if cid not in catalog_ids]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown catalog ids: {', '.join(sorted(unknown))}",
        )

    decisions = store.record_approvals(
        inv_id, payload.decisions, user.actor, payload.note
    )
    store.set_investigation_status(inv_id, "pending_approval")
    return _approval_response(inv_id, decisions)


@router.post(
    "/investigations/{inv_id}/run",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    inv_id: int,
    payload: StartRunRequest | None = None,
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> RunAccepted:
    inv = _require_inv(store, inv_id)
    if inv.status not in ("draft", "pending_approval"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run already started",
        )
    decisions = store.get_approvals(inv_id) or {}
    if _approval_state(decisions) != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval gate not satisfied",
        )

    credentials = payload.credentials if payload else None
    if credentials is not None and inv.auth_type == "sql" and (
        not credentials.username or credentials.password is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SQL authentication requires a username and password",
        )

    job = store.create_job(inv_id)
    logger.info(
        "run started for investigation %s on %s:%s (%s) by %s",
        inv_id,
        inv.server_host,
        inv.server_port,
        "live collection" if credentials is not None else "demo report",
        user.actor,
    )

    # Credentials present → live collection (#31). The password lives only
    # in this request frame: it is handed to the driver, never stored, and
    # nothing below this line receives it.
    collection: list[dict[str, Any]] | None = None
    if credentials is not None:
        approved_ids = {
            cid for cid, decision in decisions.items() if decision.decision == "approved"
        }
        entries = [
            {
                "id": entry.id,
                "title": entry.title,
                "category": entry.category,
                "issue_area": entry.issue_area,
                "sql": entry.sql,
            }
            for entry in inv.catalog
            if entry.id in approved_ids
        ]
        outcomes = live.run_collection(
            entries,
            live.ConnectionSettings(
                server_host=inv.server_host,
                server_port=inv.server_port,
                auth_type=inv.auth_type,
                username=credentials.username,
                password=credentials.password,
                timeout_seconds=live.query_timeout_seconds(),
            ),
            lookback_hours=inv.lookback_hours,
            databases=inv.databases,
        )
        collection = [outcome.to_dict() for outcome in outcomes]

    # Without credentials the run keeps the seeded demo report so the
    # dashboard always has something to render.
    store.update_job(job.job_id, status="succeeded", result={"investigation_id": inv_id})
    store.set_investigation_status(inv_id, "completed")
    store.generate_report(inv, user.organization, collection=collection)
    return RunAccepted(job_id=job.job_id)


__all__ = ["CatalogEntry", "router"]
