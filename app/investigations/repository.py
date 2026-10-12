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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Approval, Investigation, Organization
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

    def get_investigation(self, investigation_id: int) -> InvestigationRecord | None:
        """Return the investigation with ``investigation_id``, or ``None``."""

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

    def update_status(self, investigation_id: int, status: str) -> InvestigationRecord | None:
        """Move an investigation to a new run status (#11); None if unknown.

        Called by the enqueue path (draft -> running) and the worker
        (running -> succeeded/failed) so the investigation reflects its job.
        """


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

    def get_investigation(self, investigation_id: int) -> InvestigationRecord | None:
        with self._session_factory() as session:
            model = session.get(Investigation, investigation_id)
            return _to_record(model) if model is not None else None

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

    def update_status(self, investigation_id: int, status: str) -> InvestigationRecord | None:
        with self._session_factory() as session:
            model = session.get(Investigation, investigation_id)
            if model is None:
                return None
            model.status = status
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

    def get_investigation(self, investigation_id: int) -> InvestigationRecord | None:
        for row in self.rows:
            if row.id == investigation_id:
                return row
        return None

    def set_status(self, investigation_id: int, status: str) -> None:
        """Test helper: simulate a run moving the investigation out of draft."""
        for index, row in enumerate(self.rows):
            if row.id == investigation_id:
                self.rows[index] = replace(row, status=status)
                return

    def update_status(self, investigation_id: int, status: str) -> InvestigationRecord | None:
        """Move the investigation to a new run status; None if it is unknown."""
        for index, row in enumerate(self.rows):
            if row.id == investigation_id:
                updated = replace(row, status=status)
                self.rows[index] = updated
                return updated
        return None

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


@dataclass(frozen=True)
class ApprovalRecord:
    """One append-only approval decision row."""

    id: int
    investigation_id: int
    catalog_id: str
    decision: str
    actor: str
    note: str | None
    created_at: datetime


@runtime_checkable
class ApprovalRepository(Protocol):
    """Append-only reads/writes of per-query approval decisions.

    Rows are never updated or deleted; a changed decision is a new row. The
    "current" decision for a catalog id is simply the last row appended for it.
    """

    def list_decisions(self, investigation_id: int) -> list[ApprovalRecord]:
        """Every decision row for ``investigation_id`` in append order."""

    def latest_decisions(
        self, investigation_id: int
    ) -> dict[str, ApprovalRecord]:
        """The last decision per catalog id for ``investigation_id``."""

    def append_decision(
        self,
        *,
        investigation_id: int,
        catalog_id: str,
        decision: str,
        actor: str,
        note: str | None,
    ) -> ApprovalRecord:
        """Append and return a new decision row."""


def _approval_to_record(model: Approval) -> ApprovalRecord:
    return ApprovalRecord(
        id=model.id,
        investigation_id=model.investigation_id,
        catalog_id=model.catalog_id,
        decision=model.decision,
        actor=model.actor,
        note=model.note,
        created_at=model.created_at,
    )


class SqlAlchemyApprovalRepository:
    """Real append-only approval repository (via #3/#4 sessions)."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]] | None = None,
    ) -> None:
        self._session_factory = session_factory or session_scope

    def list_decisions(self, investigation_id: int) -> list[ApprovalRecord]:
        with self._session_factory() as session:
            statement = (
                select(Approval)
                .where(Approval.investigation_id == investigation_id)
                .order_by(Approval.id)
            )
            return [
                _approval_to_record(model)
                for model in session.execute(statement).scalars()
            ]

    def latest_decisions(
        self, investigation_id: int
    ) -> dict[str, ApprovalRecord]:
        latest: dict[str, ApprovalRecord] = {}
        for record in self.list_decisions(investigation_id):
            latest[record.catalog_id] = record
        return latest

    def append_decision(
        self,
        *,
        investigation_id: int,
        catalog_id: str,
        decision: str,
        actor: str,
        note: str | None,
    ) -> ApprovalRecord:
        with self._session_factory() as session:
            model = Approval(
                investigation_id=investigation_id,
                catalog_id=catalog_id,
                decision=decision,
                actor=actor,
                note=note,
            )
            session.add(model)
            session.flush()
            return _approval_to_record(model)


class InMemoryApprovalRepository:
    """In-memory append-only approval repository for unit tests."""

    def __init__(self) -> None:
        self.rows: list[ApprovalRecord] = []
        self._next_id = 1

    def list_decisions(self, investigation_id: int) -> list[ApprovalRecord]:
        return [row for row in self.rows if row.investigation_id == investigation_id]

    def latest_decisions(
        self, investigation_id: int
    ) -> dict[str, ApprovalRecord]:
        latest: dict[str, ApprovalRecord] = {}
        for record in self.list_decisions(investigation_id):
            latest[record.catalog_id] = record
        return latest

    def append_decision(
        self,
        *,
        investigation_id: int,
        catalog_id: str,
        decision: str,
        actor: str,
        note: str | None,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            id=self._next_id,
            investigation_id=investigation_id,
            catalog_id=catalog_id,
            decision=decision,
            actor=actor,
            note=note,
            created_at=datetime.now(UTC),
        )
        self._next_id += 1
        self.rows.append(record)
        return record
