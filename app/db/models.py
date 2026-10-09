"""SQLAlchemy models for the shared repository control database.

``Organization`` is the first domain table. It is created by the Alembic
migration ``0002_organization`` and lives in the control database named by
``DATABASE_URL``; each organization's isolated database is derived from its
``id`` (see :mod:`app.provisioning`).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Identity, Unicode
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
