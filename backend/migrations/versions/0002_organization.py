"""organization table

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09

Creates the ``organization`` table in the shared repository control database:
an identity primary key plus a unique display name and a unique OIDC issuer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the organization table."""
    op.create_table(
        "organization",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("name", sa.Unicode(length=255), nullable=False),
        sa.Column("sso_issuer", sa.Unicode(length=512), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("name", name="uq_organization_name"),
        sa.UniqueConstraint("sso_issuer", name="uq_organization_sso_issuer"),
    )


def downgrade() -> None:
    """Drop the organization table."""
    op.drop_table("organization")
