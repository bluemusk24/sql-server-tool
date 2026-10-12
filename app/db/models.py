"""SQLAlchemy models for the shared repository databases.

``Organization`` is the control-database table created by the Alembic
migration ``0002_organization`` in the database named by ``DATABASE_URL``;
each organization's isolated database is derived from its ``id`` (see
:mod:`app.provisioning`).

``Investigation`` is the first domain table. Per the #9 groomed spec it is
written to the organization's repository database (the one provisioned by
:mod:`app.provisioning`) and holds the DBA's inputs before approval. It never
holds credentials: those live only in the session-only
:class:`app.credentials.CredentialStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    Unicode,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp used as the Python-side default."""
    return datetime.now(UTC)


def _identity_type() -> BigInteger:
    """Identity primary-key type, portable to SQLite for the unit tests.

    SQL Server uses ``BIGINT IDENTITY``; SQLite has no identity columns, so the
    metadata-driven DDL (used by the ``sqlite://`` unit tests, never by Alembic)
    falls back to an ``INTEGER`` primary key that autoincrements via rowid.
    """
    return BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """Declarative base shared by every repository model."""


class AppendOnlyViolation(RuntimeError):
    """Raised when code tries to mutate an append-only row after insert."""


class Organization(Base):
    """A tenant registered in the shared repository control database."""

    __tablename__ = "organization"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=1, increment=1),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(Unicode(255), nullable=False, unique=True)
    sso_issuer: Mapped[str] = mapped_column(
        Unicode(512), nullable=False, unique=True
    )

    def __repr__(self) -> str:
        return f"Organization(id={self.id!r}, name={self.name!r})"


class Investigation(Base):
    """A DBA's investigation request, captured before any diagnostics run.

    The row records the server, scope, and lookback window only. Credentials
    arrive separately through the session-only credential store keyed by this
    row's ``id`` and are never persisted here.
    """

    __tablename__ = "investigation"
    __table_args__ = (
        CheckConstraint(
            "auth_type IN ('sql', 'ad')", name="ck_investigation_auth_type"
        ),
        CheckConstraint(
            "scope_kind IN ('instance', 'databases')",
            name="ck_investigation_scope_kind",
        ),
        CheckConstraint(
            "lookback_hours IN (1, 4, 8, 24)",
            name="ck_investigation_lookback_hours",
        ),
        Index("ix_investigation_organization_id", "organization_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=1, increment=1),
        primary_key=True,
    )
    organization_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server_host: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    server_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1433
    )
    auth_type: Mapped[str] = mapped_column(Unicode(16), nullable=False)
    scope_kind: Mapped[str] = mapped_column(Unicode(16), nullable=False)
    databases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    lookback_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Unicode(16), nullable=False, default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"Investigation(id={self.id!r}, organization_id={self.organization_id!r}, "
            f"status={self.status!r})"
        )


class Approval(Base):
    """One DBA decision on one catalog query for one investigation.

    Rows are *append-only*: a decision change is a new row, never an update, so
    the #21 audit trail keeps every decision in order. The model enforces this
    with ``before_update``/``before_delete`` listeners (see below) so the rule
    cannot be bypassed by ORM code.
    """

    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_approval_decision"
        ),
        Index("ix_approval_investigation_id", "investigation_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=1, increment=1),
        primary_key=True,
    )
    investigation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_id: Mapped[str] = mapped_column(Unicode(128), nullable=False)
    decision: Mapped[str] = mapped_column(Unicode(16), nullable=False)
    actor: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    note: Mapped[str | None] = mapped_column(Unicode(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"Approval(id={self.id!r}, investigation_id={self.investigation_id!r}, "
            f"catalog_id={self.catalog_id!r}, decision={self.decision!r})"
        )


@event.listens_for(Approval, "before_update")
def _prevent_approval_update(mapper: object, connection: object, target: Approval) -> None:
    """Reject updates: the approval trail is append-only."""
    raise AppendOnlyViolation("approval rows are append-only and cannot be updated")


@event.listens_for(Approval, "before_delete")
def _prevent_approval_delete(mapper: object, connection: object, target: Approval) -> None:
    """Reject deletes: the approval trail is append-only."""
    raise AppendOnlyViolation("approval rows are append-only and cannot be deleted")


class EvidenceRun(Base):
    """Raw evidence from one catalog query in one investigation (#12/#21).

    One row per :class:`~app.evidence.models.QueryEvidence` a diagnostic run
    produced. ``results`` holds the raw result rows; ``source``, ``status``,
    ``duration`` and ``error`` mirror the same model's scalar fields. The row
    carries no credential material: it is redacted and size-capped by
    :mod:`app.persistence` before it is written.
    """

    __tablename__ = "evidence_run"
    __table_args__ = (
        Index("ix_evidence_run_investigation_id", "investigation_id"),
    )

    id: Mapped[int] = mapped_column(
        _identity_type(), Identity(start=1, increment=1), primary_key=True
    )
    investigation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(Unicode(128), nullable=False)
    status: Mapped[str] = mapped_column(Unicode(32), nullable=False)
    results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    duration: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Unicode(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"EvidenceRun(id={self.id!r}, investigation_id={self.investigation_id!r}, "
            f"source={self.source!r}, status={self.status!r})"
        )


class Finding(Base):
    """One normalized rule signal persisted for an investigation (#17/#21).

    The columns are exactly the #17
    :class:`~app.rules.signals.Signal` fields: ``area``, ``severity``,
    ``description`` and the referenced evidence ids in ``evidence_refs``.
    """

    __tablename__ = "finding"
    __table_args__ = (Index("ix_finding_investigation_id", "investigation_id"),)

    id: Mapped[int] = mapped_column(
        _identity_type(), Identity(start=1, increment=1), primary_key=True
    )
    investigation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    area: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    severity: Mapped[str] = mapped_column(Unicode(16), nullable=False)
    description: Mapped[str] = mapped_column(Unicode(2048), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"Finding(id={self.id!r}, investigation_id={self.investigation_id!r}, "
            f"area={self.area!r}, severity={self.severity!r})"
        )


class VerdictRecord(Base):
    """One #18 correlation verdict persisted as its JSON payload (#21).

    ``payload`` is :meth:`app.ai.schemas.Verdict.model_dump` output, so the
    verdict model stays the single source of truth for the stored shape.
    """

    __tablename__ = "verdict"
    __table_args__ = (Index("ix_verdict_investigation_id", "investigation_id"),)

    id: Mapped[int] = mapped_column(
        _identity_type(), Identity(start=1, increment=1), primary_key=True
    )
    investigation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"VerdictRecord(id={self.id!r}, "
            f"investigation_id={self.investigation_id!r})"
        )


class ReportPayloadRecord(Base):
    """The assembled #19 report payload, one row per investigation (#21).

    ``payload`` is :meth:`app.report.schemas.ReportPayload.model_dump` output.
    ``investigation_id`` is unique: storing a newer payload for the same
    investigation replaces the row (newest wins).
    """

    __tablename__ = "report_payload"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id", name="uq_report_payload_investigation_id"
        ),
    )

    id: Mapped[int] = mapped_column(
        _identity_type(), Identity(start=1, increment=1), primary_key=True
    )
    investigation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"ReportPayloadRecord(id={self.id!r}, "
            f"investigation_id={self.investigation_id!r})"
        )


class Audit(Base):
    """One append-only audit trail entry (#21).

    Rows are *append-only*: there is no update path, enforced with a
    ``before_update`` listener so ORM code cannot rewrite history. Deletes are
    allowed only for the retention cleanup. ``actor``, ``action`` and ``target``
    are redacted before insert and never carry credential material. There is no
    organization column: each table lives in its own organization's database,
    so the organization is implicit.
    """

    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(
        _identity_type(), Identity(start=1, increment=1), primary_key=True
    )
    actor: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    action: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    target: Mapped[str] = mapped_column(Unicode(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"Audit(id={self.id!r}, action={self.action!r}, "
            f"actor={self.actor!r}, target={self.target!r})"
        )


@event.listens_for(Audit, "before_update")
def _prevent_audit_update(mapper: object, connection: object, target: Audit) -> None:
    """Reject updates: the audit trail is append-only."""
    raise AppendOnlyViolation("audit rows are append-only and cannot be updated")
