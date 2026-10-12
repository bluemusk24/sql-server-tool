"""persistence, audit and retention tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12

Creates the #21 persistence tables in the organization's repository database
(provisioned by #4): ``evidence_run`` (raw #12 evidence), ``finding`` (#17
signals), ``verdict`` (#18), ``report_payload`` (#19, one row per investigation)
and the append-only ``audit`` trail. No column holds credential material; the
retention cleanup (see :mod:`app.persistence`) removes rows older than 7 days.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the persistence, audit and retention tables."""
    op.create_table(
        "evidence_run",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Unicode(length=128), nullable=False),
        sa.Column("status", sa.Unicode(length=32), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("error", sa.Unicode(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_run"),
    )
    op.create_index(
        "ix_evidence_run_investigation_id", "evidence_run", ["investigation_id"]
    )

    op.create_table(
        "finding",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("area", sa.Unicode(length=64), nullable=False),
        sa.Column("severity", sa.Unicode(length=16), nullable=False),
        sa.Column("description", sa.Unicode(length=2048), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finding"),
    )
    op.create_index(
        "ix_finding_investigation_id", "finding", ["investigation_id"]
    )

    op.create_table(
        "verdict",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_verdict"),
    )
    op.create_index(
        "ix_verdict_investigation_id", "verdict", ["investigation_id"]
    )

    op.create_table(
        "report_payload",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_payload"),
        sa.UniqueConstraint(
            "investigation_id", name="uq_report_payload_investigation_id"
        ),
    )

    op.create_table(
        "audit",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("actor", sa.Unicode(length=255), nullable=False),
        sa.Column("action", sa.Unicode(length=64), nullable=False),
        sa.Column("target", sa.Unicode(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit"),
    )


def downgrade() -> None:
    """Drop the persistence, audit and retention tables."""
    op.drop_table("audit")
    op.drop_table("report_payload")
    op.drop_index("ix_verdict_investigation_id", table_name="verdict")
    op.drop_table("verdict")
    op.drop_index("ix_finding_investigation_id", table_name="finding")
    op.drop_table("finding")
    op.drop_index("ix_evidence_run_investigation_id", table_name="evidence_run")
    op.drop_table("evidence_run")
