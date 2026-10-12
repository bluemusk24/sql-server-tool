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
    get_audit_writer,
    get_caller,
    get_catalog,
    get_investigation_repository,
)
from app.investigations.repository import AuditWriter, InvestigationRepository
from app.investigations.schemas import InvestigationCreate, InvestigationResponse
from app.investigations.service import (
    AdAuthUnsupported,
    UnknownOrganization,
    create_investigation,
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
