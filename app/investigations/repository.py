"""Persistence for investigations, kept behind a small interface.

The endpoint depends on :class:`InvestigationRepository`, not on SQLAlchemy
directly. That keeps the create flow unit-testable with
:class:`InMemoryInvestigationRepository` (no SQL Server required) while
:class:`SqlAlchemyInvestigationRepository` is the real path, exercised only by
the ``RUN_DB_TESTS=1`` integration tests.

The audit write is also an interface: :class:`AuditWriter`. The #9 spec defers
the audit table to #21, so the endpoint's default writer is the no-op
:class:`NullAuditWriter`; when #21 adds the table it can supply a real writer
without touching this endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Investigation, Organization
from app.db.session import session_scope


@dataclass(frozen=True)
class InvestigationRecord:
    """A persisted investigation, without any credential material."""

    id: int
    organization_id: int
    server_host: str
    server_port: int
    auth_type: str
    scope_kind: str
    databases: list[str] | None
    lookback_hours: int
    status: str
    created_at: datetime


@dataclass(frozen=True)
class AuditEntry:
    """One append-only audit event (actor, action, target)."""

    organization_id: int
    actor: str
    action: str
    target: str


@runtime_checkable
class AuditWriter(Protocol):
    """Records audit events; the concrete table lands with #21."""

    def write(self, entry: AuditEntry) -> None:
        """Append ``entry`` to the audit trail."""


class NullAuditWriter:
    """Default audit writer: defers the actual write to #21.

    The endpoint still calls :meth:`write`; this implementation deliberately
    does nothing so that creating an investigation works before the audit table
    exists. #21 swaps in a real writer.
    """

    def write(self, entry: AuditEntry) -> None:
        return None


@runtime_checkable
class InvestigationRepository(Protocol):
    """Read/write operations the create endpoint needs."""

    def get_organization_id_by_issuer(self, issuer: str) -> int | None:
        """Return the organization id registered for ``issuer``, or ``None``."""

    def create_investigation(
        self,
        *,
        organization_id: int,
        server_host: str,
        server_port: int,
        auth_type: str,
        scope_kind: str,
        databases: list[str] | None,
        lookback_hours: int,
        status: str,
    ) -> InvestigationRecord:
        """Persist and return a new investigation row."""


def _to_record(model: Investigation) -> InvestigationRecord:
    return InvestigationRecord(
        id=model.id,
        organization_id=model.organization_id,
        server_host=model.server_host,
        server_port=model.server_port,
        auth_type=model.auth_type,
        scope_kind=model.scope_kind,
        databases=list(model.databases) if model.databases is not None else None,
        lookback_hours=model.lookback_hours,
        status=model.status,
        created_at=model.created_at,
    )


class SqlAlchemyInvestigationRepository:
    """Real repository backed by the repository SQL Server (via #3/#4)."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
    ) -> None:
        self._session_factory = session_factory or session_scope

    def get_organization_id_by_issuer(self, issuer: str) -> int | None:
        with self._session_factory() as session:
            statement = select(Organization.id).where(
                Organization.sso_issuer == issuer
            )
            return session.execute(statement).scalar_one_or_none()

    def create_investigation(
        self,
        *,
        organization_id: int,
        server_host: str,
        server_port: int,
        auth_type: str,
        scope_kind: str,
        databases: list[str] | None,
        lookback_hours: int,
        status: str,
    ) -> InvestigationRecord:
        with self._session_factory() as session:
            model = Investigation(
                organization_id=organization_id,
                server_host=server_host,
                server_port=server_port,
                auth_type=auth_type,
                scope_kind=scope_kind,
                databases=databases,
                lookback_hours=lookback_hours,
                status=status,
            )
            session.add(model)
            session.flush()
            return _to_record(model)


class InMemoryInvestigationRepository:
    """In-memory repository for unit tests (no SQL Server required)."""

    def __init__(self, organizations_by_issuer: dict[str, int] | None = None) -> None:
        self.organizations_by_issuer = dict(organizations_by_issuer or {})
        self.rows: list[InvestigationRecord] = []
        self._next_id = 1

    def get_organization_id_by_issuer(self, issuer: str) -> int | None:
        return self.organizations_by_issuer.get(issuer)

    def create_investigation(
        self,
        *,
        organization_id: int,
        server_host: str,
        server_port: int,
        auth_type: str,
        scope_kind: str,
        databases: list[str] | None,
        lookback_hours: int,
        status: str,
    ) -> InvestigationRecord:
        record = InvestigationRecord(
            id=self._next_id,
            organization_id=organization_id,
            server_host=server_host,
            server_port=server_port,
            auth_type=auth_type,
            scope_kind=scope_kind,
            databases=list(databases) if databases is not None else None,
            lookback_hours=lookback_hours,
            status=status,
            created_at=datetime.now(UTC),
        )
        self._next_id += 1
        self.rows.append(record)
        return record


class RecordingAuditWriter:
    """In-memory audit writer that keeps what it was asked to record."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def write(self, entry: AuditEntry) -> None:
        self.entries.append(entry)
