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

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    Unicode,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp used as the Python-side default."""
    return datetime.now(UTC)


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
