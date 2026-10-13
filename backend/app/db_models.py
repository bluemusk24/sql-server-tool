"""SQLAlchemy ORM tables.

These are the relational schema for the store. Types are chosen to be
portable across databases: ``JSON`` works on both SQLite and Postgres, and
the string/integer columns avoid vendor-specific types so switching
``DATABASE_URL`` to Postgres later needs no model changes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every table in the application."""


class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str] = mapped_column(String(512))


class UserRow(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization: Mapped[str] = mapped_column(String(255))
    actor: Mapped[str] = mapped_column(String(255))
    password_salt: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))


class AuthTokenRow(Base):
    __tablename__ = "auth_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("users.username"), index=True)
    created_at: Mapped[str] = mapped_column(String(64))


class InvestigationRow(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    server_host: Mapped[str] = mapped_column(String(255))
    server_port: Mapped[int] = mapped_column(Integer)
    auth_type: Mapped[str] = mapped_column(String(16))
    databases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    lookback_hours: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(64))


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "catalog_id", name="uq_approval_inv_catalog"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id"), index=True
    )
    catalog_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))


class JobRow(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    investigation_id: Mapped[int | None] = mapped_column(
        ForeignKey("investigations.id"), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReportRow(Base):
    __tablename__ = "reports"

    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id"), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
