"""Investigations router: create/list/get, approvals, and run gating.

All endpoints here require an authenticated session (bearer token). The
approval gate enforces the spec's 409 semantics: decisions cannot change
once a run has started, and a run cannot start until the gate is approved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

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
)
from app.store import ApprovalDecision, InMemoryStore, Investigation, get_store

router = APIRouter(tags=["investigations"])


def _require_inv(store: InMemoryStore, inv_id: int) -> Investigation:
    inv = store.get_investigation(inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return inv


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
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> list[InvestigationSummary]:
    return [
        InvestigationSummary(id=row["id"], status=row["status"])
        for row in store.list_investigations()
    ]


@router.post(
    "/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_investigation(
    payload: CreateInvestigationRequest,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> InvestigationResponse:
    inv = store.create_investigation(
        server_host=payload.server_host,
        server_port=payload.server_port,
        auth_type=payload.auth_type.value,
        databases=payload.databases,
        lookback_hours=payload.lookback_hours,
    )
    return InvestigationResponse(
        id=inv.id, status=inv.status, catalog=list(inv.catalog)
    )


@router.get("/investigations/{inv_id}", response_model=InvestigationResponse)
def get_investigation(
    inv_id: int,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> InvestigationResponse:
    inv = _require_inv(store, inv_id)
    return InvestigationResponse(
        id=inv.id, status=inv.status, catalog=list(inv.catalog)
    )


@router.get("/investigations/{inv_id}/approvals", response_model=ApprovalResponse)
def get_approvals(
    inv_id: int,
    store: InMemoryStore = Depends(get_store),
    _: CurrentUser = Depends(get_current_user),
) -> ApprovalResponse:
    _require_inv(store, inv_id)
    decisions = store.get_approvals(inv_id) or {}
    return _approval_response(inv_id, decisions)


@router.post("/investigations/{inv_id}/approvals", response_model=ApprovalResponse)
def record_decisions(
    inv_id: int,
    payload: ApprovalRequest,
    store: InMemoryStore = Depends(get_store),
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

    decisions = store.approvals.setdefault(inv_id, {})
    now = datetime.now(UTC).isoformat()
    for cid, decision in payload.decisions.items():
        decisions[cid] = ApprovalDecision(
            catalog_id=cid,
            decision=decision,
            actor=user.actor,
            note=payload.note,
            created_at=now,
        )
    inv.status = "pending_approval"
    return _approval_response(inv_id, decisions)


@router.post(
    "/investigations/{inv_id}/run",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    inv_id: int,
    store: InMemoryStore = Depends(get_store),
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

    # The in-memory "run" succeeds immediately and produces a report so the
    # dashboard has something to render. A real backend would enqueue here.
    job = store.create_job(inv_id)
    job.status = "succeeded"
    job.result = {"investigation_id": inv_id}
    inv.status = "completed"
    store.generate_report(inv, user.organization)
    return RunAccepted(job_id=job.job_id)


__all__ = ["CatalogEntry", "router"]
