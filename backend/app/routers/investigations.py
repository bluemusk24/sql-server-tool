"""Investigations router: create/list/get, approvals, and run gating.

All endpoints here require an authenticated session (bearer token). The
approval gate enforces the spec's 409 semantics: decisions cannot change
once a run has started, and a run cannot start until the gate is approved.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

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
    request: Request,
    payload: CreateInvestigationRequest,
    store: DatabaseStore = Depends(get_store),
    user: CurrentUser = Depends(get_current_user),
) -> InvestigationResponse:
    try:
        inv = store.create_investigation(
            server_host=payload.server_host,
            server_port=payload.server_port,
            auth_type=payload.auth_type.value,
            databases=payload.databases,
            lookback_hours=payload.lookback_hours,
        )
    except Exception:
        request.app.state.metrics.creation_failed("investigation")
        raise
    request.app.state.metrics.investigation_created(user.actor)
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


def _execute_run(
    *,
    session_factory: Callable[[], Session],
    job_id: str,
    inv_id: int,
    organization: str,
    entries: list[dict[str, Any]] | None,
    settings: live.ConnectionSettings | None,
    lookback_hours: int,
    databases: list[str] | None,
) -> None:
    """Execute a diagnostic run on a worker thread (#29).

    Runs outside the request's session — it owns a fresh one — so it can
    outlive the 202 response. Cancellation is cooperative: the collection
    loop re-reads the job row between sources, and ``POST /jobs/{id}/cancel``
    flips it to ``canceled``, stopping the run while keeping the partial
    evidence. The overall budget (``LIVE_RUN_TIMEOUT_SECONDS``) stops
    runaway runs the same way.
    """
    session = session_factory()
    try:
        store = DatabaseStore(session)
        inv = store.get_investigation(inv_id)
        if inv is None:
            return
        current = store.get_job(job_id)
        if current is not None and current.status == "canceled":
            # Canceled before the worker even got going (#29): keep the
            # state and still publish a report for the investigation.
            store.set_investigation_status(inv_id, "canceled")
            store.generate_report(inv, organization, collection=None)
            session.commit()
            return
        store.update_job(job_id, status="running")

        timed_out = False
        collection: list[dict[str, Any]] | None = None
        if entries is not None and settings is not None:

            def _canceled() -> bool:
                current = store.get_job(job_id)
                return current is not None and current.status == "canceled"

            try:
                outcomes = live.run_collection(
                    entries,
                    settings,
                    lookback_hours,
                    databases,
                    deadline_seconds=live.run_timeout_seconds(),
                    should_abort=_canceled,
                )
            except live.RunTimeout as exc:
                outcomes = exc.outcomes
                timed_out = True
            if _canceled() or timed_out:
                # Sources never reached become explicit coverage gaps
                # instead of silently vanishing from the report (#29).
                outcomes = live.mark_uncollected(
                    entries,
                    outcomes,
                    "run stopped before this source was collected",
                )
            collection = [outcome.to_dict() for outcome in outcomes]

        current = store.get_job(job_id)
        if current is not None and current.status == "canceled":
            store.set_investigation_status(inv_id, "canceled")
        elif timed_out:
            store.update_job(
                job_id,
                status="failed",
                error=(
                    "run exceeded the overall timeout "
                    f"({live.run_timeout_seconds()}s)"
                ),
            )
            store.set_investigation_status(inv_id, "failed")
        else:
            store.update_job(
                job_id, status="succeeded", result={"investigation_id": inv_id}
            )
            store.set_investigation_status(inv_id, "completed")
        # A canceled or timed-out run still keeps what it gathered — the
        # report marks the rest as coverage gaps.
        store.generate_report(inv, organization, collection=collection)
        session.commit()
    except Exception:
        logger.exception("run %s failed", job_id)
        session.rollback()
        try:
            DatabaseStore(session).update_job(
                job_id, status="failed", error="run failed; see server logs"
            )
            session.commit()
        except Exception:
            logger.exception("could not record failure for job %s", job_id)
    finally:
        session.close()


@router.post(
    "/investigations/{inv_id}/run",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    inv_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
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
    # Commit the job row before the worker thread opens its own database
    # session (#29): across a real connection pool (SQLite file, Postgres)
    # the worker cannot see this request's uncommitted rows — the in-memory
    # test database shares one connection, which hides the difference.
    store.session.commit()
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
    entries: list[dict[str, Any]] | None = None
    settings: live.ConnectionSettings | None = None
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
        settings = live.ConnectionSettings(
            server_host=inv.server_host,
            server_port=inv.server_port,
            auth_type=inv.auth_type,
            username=credentials.username,
            password=credentials.password,
            timeout_seconds=live.query_timeout_seconds(),
        )

    # Execute on a worker thread with its own DB session (#29): the 202
    # returns the job id immediately, so the job status surface can be
    # polled — and the run canceled — while collection is in flight.
    background_tasks.add_task(
        _execute_run,
        session_factory=request.app.state.SessionLocal,
        job_id=job.job_id,
        inv_id=inv_id,
        organization=user.organization,
        entries=entries,
        settings=settings,
        lookback_hours=inv.lookback_hours,
        databases=inv.databases,
    )
    return RunAccepted(job_id=job.job_id)


__all__ = ["CatalogEntry", "router"]
