"""Business logic for creating an investigation.

The service is transport-agnostic: it takes a validated payload, the caller
context, a repository, and an audit writer. The router maps its domain
exceptions onto HTTP status codes.
"""

from __future__ import annotations

from app.diagnostics.catalog import Catalog
from app.investigations.repository import (
    ApprovalRecord,
    ApprovalRepository,
    AuditEntry,
    AuditWriter,
    InvestigationRecord,
    InvestigationRepository,
)
from app.investigations.schemas import (
    ApprovalQuery,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalState,
    AuthType,
    InvestigationCreate,
    ScopeKind,
)

#: Action recorded in the audit trail for a created investigation.
CREATE_ACTION = "investigation.create"

#: Action recorded in the audit trail for a recorded approval decision.
APPROVAL_ACTION = "approval.decide"


class AdAuthUnsupported(Exception):
    """AD/Kerberos auth was requested before #28 ships (mapped to 501)."""


class UnknownOrganization(Exception):
    """The caller's SSO issuer is not a registered organization (#30 auth)."""


class UnknownInvestigation(Exception):
    """No investigation exists for the requested id (mapped to 404)."""


class UnknownCatalogQuery(Exception):
    """A decision names a catalog id that is unknown or needs no approval (422)."""


class ApprovalLocked(Exception):
    """A decision change was attempted after the run started (mapped to 409)."""


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


def required_catalog_ids(catalog: Catalog) -> list[str]:
    """The catalog ids that must be explicitly approved before a run.

    Only queries flagged ``requires_approval`` are gated (catalog.md §6.6); a
    query that does not need approval is not part of the decision set.
    """
    return [entry.id for entry in catalog.all() if entry.requires_approval]


def compute_state(
    latest: dict[str, ApprovalRecord], required_ids: list[str]
) -> ApprovalState:
    """Derive the three-state approval state from the latest decisions.

    ``rejected`` wins as soon as any required query is rejected; otherwise
    ``approved`` only when every required query has an ``approved`` decision;
    otherwise ``pending``.
    """
    decisions = [
        latest[cid].decision if cid in latest else None for cid in required_ids
    ]
    if "rejected" in decisions:
        return ApprovalState.rejected
    if all(decision == "approved" for decision in decisions):
        return ApprovalState.approved
    return ApprovalState.pending


def approval_state(
    investigation_id: int,
    *,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
) -> ApprovalState:
    """Return the computed approval state for an existing investigation."""
    latest = approval_repository.latest_decisions(investigation_id)
    return compute_state(latest, required_catalog_ids(catalog))


def can_start(
    investigation_id: int,
    *,
    investigation_repository: InvestigationRepository,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
) -> bool:
    """The single run gate shared with #12.

    A run may start only when the investigation exists and every catalog query
    that requires approval has been approved. #12 calls this instead of
    reimplementing the rule; it also owns refusing a second start of a run that
    is already underway.
    """
    if investigation_repository.get_investigation(investigation_id) is None:
        return False
    return (
        approval_state(
            investigation_id,
            approval_repository=approval_repository,
            catalog=catalog,
        )
        is ApprovalState.approved
    )


def build_approval_response(
    investigation_id: int,
    *,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
) -> ApprovalResponse:
    """Build the per-query decisions plus the computed state."""
    required_ids = required_catalog_ids(catalog)
    latest = approval_repository.latest_decisions(investigation_id)
    queries: list[ApprovalQuery] = []
    for catalog_id in required_ids:
        record = latest.get(catalog_id)
        queries.append(
            ApprovalQuery(
                catalog_id=catalog_id,
                decision=record.decision if record is not None else None,
                actor=record.actor if record is not None else None,
                note=record.note if record is not None else None,
                created_at=record.created_at if record is not None else None,
            )
        )
    return ApprovalResponse(
        investigation_id=investigation_id,
        state=compute_state(latest, required_ids),
        queries=queries,
    )


def record_decisions(
    investigation_id: int,
    payload: ApprovalRequest,
    *,
    caller_actor: str,
    investigation_repository: InvestigationRepository,
    approval_repository: ApprovalRepository,
    catalog: Catalog,
    audit_writer: AuditWriter,
) -> ApprovalResponse:
    """Record a batch of decisions, appending only rows that change state.

    Validation (unknown/non-approvable ids, and any change after the run
    started) happens before appending, so a rejected request writes nothing.
    Repeating the same decision for the same query is idempotent: no new row,
    still ``200``.
    """
    investigation = investigation_repository.get_investigation(investigation_id)
    if investigation is None:
        raise UnknownInvestigation(f"no investigation with id {investigation_id}")

    for catalog_id in payload.decisions:
        entry = catalog.get(catalog_id)
        if entry is None or not entry.requires_approval:
            raise UnknownCatalogQuery(
                f"catalog id {catalog_id!r} is unknown or does not require approval"
            )

    latest = approval_repository.latest_decisions(investigation_id)

    appends: list[tuple[str, str]] = []
    for catalog_id, decision in payload.decisions.items():
        existing = latest.get(catalog_id)
        if existing is not None and existing.decision == decision.value:
            continue
        if investigation.status != "draft":
            raise ApprovalLocked(
                "approval decisions cannot change after the run has started"
            )
        appends.append((catalog_id, decision.value))

    for catalog_id, decision in appends:
        approval_repository.append_decision(
            investigation_id=investigation_id,
            catalog_id=catalog_id,
            decision=decision,
            actor=caller_actor,
            note=payload.note,
        )
        audit_writer.write(
            AuditEntry(
                organization_id=investigation.organization_id,
                actor=caller_actor,
                action=APPROVAL_ACTION,
                target=f"investigation:{investigation_id}/query:{catalog_id}",
            )
        )

    return build_approval_response(
        investigation_id,
        approval_repository=approval_repository,
        catalog=catalog,
    )
