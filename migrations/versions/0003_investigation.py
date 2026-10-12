"""investigation table

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11

Creates the ``investigation`` domain table that captures a DBA's inputs
(server, auth type, coverage scope, lookback window) before approval. The row
never holds credentials; those live only in the session-only credential store.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the investigation table."""
    op.create_table(
        "investigation",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("server_host", sa.Unicode(length=255), nullable=False),
        sa.Column("server_port", sa.Integer(), nullable=False, server_default="1433"),
        sa.Column("auth_type", sa.Unicode(length=16), nullable=False),
        sa.Column("scope_kind", sa.Unicode(length=16), nullable=False),
        sa.Column("databases", sa.JSON(), nullable=True),
        sa.Column("lookback_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.Unicode(length=16), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigation"),
        sa.CheckConstraint(
            "auth_type IN ('sql', 'ad')", name="ck_investigation_auth_type"
        ),
        sa.CheckConstraint(
            "scope_kind IN ('instance', 'databases')",
            name="ck_investigation_scope_kind",
        ),
        sa.CheckConstraint(
            "lookback_hours IN (1, 4, 8, 24)",
            name="ck_investigation_lookback_hours",
        ),
    )
    op.create_index(
        "ix_investigation_organization_id", "investigation", ["organization_id"]
    )


def downgrade() -> None:
    """Drop the investigation table."""
    op.drop_index("ix_investigation_organization_id", table_name="investigation")
    op.drop_table("investigation")
