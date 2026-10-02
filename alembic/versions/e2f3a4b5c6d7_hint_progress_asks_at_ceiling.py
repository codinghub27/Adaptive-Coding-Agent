"""hint progress asks at ceiling

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ADAPTIVE-upgrade P4: Balanced mode reveals the verified solution on the
    # SECOND explicit ask made at the ladder's ceiling, so refused asks there
    # are counted per ladder. Existing rows start at 0.
    op.add_column(
        "hint_progress",
        sa.Column("asks_at_ceiling", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("hint_progress", "asks_at_ceiling")
