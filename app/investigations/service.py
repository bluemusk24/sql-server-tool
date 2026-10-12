"""Business logic for creating an investigation.

The service is transport-agnostic: it takes a validated payload, the caller
context, a repository, and an audit writer. The router maps its domain
exceptions onto HTTP status codes.
"""

from __future__ import annotations

from app.investigations.repository import (
    AuditEntry,
    AuditWriter,
    InvestigationRecord,
    InvestigationRepository,
)
from app.investigations.schemas import AuthType, InvestigationCreate, ScopeKind

#: Action recorded in the audit trail for a created investigation.
CREATE_ACTION = "investigation.create"


class AdAuthUnsupported(Exception):
    """AD/Kerberos auth was requested before #28 ships (mapped to 501)."""


class UnknownOrganization(Exception):
    """The caller's SSO issuer is not a registered organization (#30 auth)."""


def create_investigation(
    payload: InvestigationCreate,
    *,
    caller_issuer: str,
    caller_actor: str,
    repository: InvestigationRepository,
    audit_writer: AuditWriter,
) -> InvestigationRecord:
    """Persist a new investigation and record the audit event.

    Never touches credentials: they arrive separately through the session-only
    store keyed by the returned investigation id (#5).
    """
    if payload.auth_type is AuthType.ad:
        raise AdAuthUnsupported(
            "AD/Kerberos authentication is not supported yet; use auth_type=sql"
        )

    organization_id = repository.get_organization_id_by_issuer(caller_issuer)
    if organization_id is None:
        raise UnknownOrganization(
            f"no organization is registered for issuer {caller_issuer!r}"
        )

    scope_kind = (
        ScopeKind.databases if payload.databases is not None else ScopeKind.instance
    )

    record = repository.create_investigation(
        organization_id=organization_id,
        server_host=payload.server_host,
        server_port=payload.server_port,
        auth_type=payload.auth_type.value,
        scope_kind=scope_kind.value,
        databases=payload.databases,
        lookback_hours=payload.lookback_hours,
        status="draft",
    )

    audit_writer.write(
        AuditEntry(
            organization_id=organization_id,
            actor=caller_actor,
            action=CREATE_ACTION,
            target=f"investigation:{record.id}",
        )
    )
    return record
