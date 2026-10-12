"""empty baseline

Revision ID: 0001
Revises:
Create Date: 2026-10-09

This is the empty baseline migration. It creates no domain tables; running it
only establishes Alembic's own version tracking table. Domain tables are added
by later migrations.
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
