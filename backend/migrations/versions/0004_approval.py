"""approval table

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11

Creates the append-only ``approval`` table in the organization's repository
database: one row per DBA decision on a catalog query. A decision change is a
new row, never an update, so the #21 audit trail keeps the full history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only approval table."""
    op.create_table(
        "approval",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("catalog_id", sa.Unicode(length=128), nullable=False),
        sa.Column("decision", sa.Unicode(length=16), nullable=False),
        sa.Column("actor", sa.Unicode(length=255), nullable=False),
        sa.Column("note", sa.Unicode(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval"),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_approval_decision"
        ),
    )
    op.create_index(
        "ix_approval_investigation_id", "approval", ["investigation_id"]
    )


def downgrade() -> None:
    """Drop the approval table."""
    op.drop_index("ix_approval_investigation_id", table_name="approval")
    op.drop_table("approval")
