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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp used as the Python-side default."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base shared by every repository model."""


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
