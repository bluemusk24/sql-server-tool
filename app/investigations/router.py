"""HTTP routes for investigations.

``POST /investigations`` captures the DBA's inputs, persists the investigation
without credentials, and returns the fixed shape ``{id, status, catalog}``
where the catalog is the review list from #8.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.diagnostics.catalog import Catalog
from app.investigations.dependencies import (
    Caller,
    get_approval_repository,
    get_audit_writer,
    get_caller,
    get_catalog,
    get_investigation_repository,
)
from app.investigations.repository import (
    ApprovalRepository,
    AuditWriter,
    InvestigationRepository,
)
from app.investigations.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    InvestigationCreate,
    InvestigationResponse,
)
from app.investigations.service import (
    AdAuthUnsupported,
    ApprovalLocked,
    UnknownCatalogQuery,
    UnknownInvestigation,
    UnknownOrganization,
    build_approval_response,
    create_investigation,
    record_decisions,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_investigation_endpoint(
    payload: InvestigationCreate,
    caller: Annotated[Caller, Depends(get_caller)],
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
    audit_writer: Annotated[AuditWriter, Depends(get_audit_writer)],
    catalog: Annotated[Catalog, Depends(get_catalog)],
) -> InvestigationResponse:
    """Create an investigation and return its id, status, and review catalog."""
    try:
        record = create_investigation(
            payload,
            caller_issuer=caller.sso_issuer,
            caller_actor=caller.actor,
            repository=repository,
            audit_writer=audit_writer,
        )
    except AdAuthUnsupported as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except UnknownOrganization as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    return InvestigationResponse(
        id=record.id,
        status=record.status,
        catalog=[entry.to_dict() for entry in catalog.all()],
    )


@router.post("/{investigation_id}/approvals", status_code=status.HTTP_200_OK)
def decide_approvals_endpoint(
    investigation_id: int,
    payload: ApprovalRequest,
    caller: Annotated[Caller, Depends(get_caller)],
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
    approval_repository: Annotated[
        ApprovalRepository, Depends(get_approval_repository)
    ],
    audit_writer: Annotated[AuditWriter, Depends(get_audit_writer)],
    catalog: Annotated[Catalog, Depends(get_catalog)],
) -> ApprovalResponse:
    """Record approve/reject decisions for one or more catalog queries."""
    try:
        return record_decisions(
            investigation_id,
            payload,
            caller_actor=caller.actor,
            investigation_repository=repository,
            approval_repository=approval_repository,
            catalog=catalog,
            audit_writer=audit_writer,
        )
    except UnknownInvestigation as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except UnknownCatalogQuery as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ApprovalLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get("/{investigation_id}/approvals", status_code=status.HTTP_200_OK)
def get_approvals_endpoint(
    investigation_id: int,
    repository: Annotated[
        InvestigationRepository, Depends(get_investigation_repository)
    ],
    approval_repository: Annotated[
        ApprovalRepository, Depends(get_approval_repository)
    ],
    catalog: Annotated[Catalog, Depends(get_catalog)],
) -> ApprovalResponse:
    """Return per-query decisions and the computed state, or 404 if unknown."""
    if repository.get_investigation(investigation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no investigation with id {investigation_id}",
        )
    return build_approval_response(
        investigation_id,
        approval_repository=approval_repository,
        catalog=catalog,
    )
