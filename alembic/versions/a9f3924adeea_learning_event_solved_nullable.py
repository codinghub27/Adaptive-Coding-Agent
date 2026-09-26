"""learning event solved nullable

Revision ID: a9f3924adeea
Revises: c2bfb7a6c2b8
Create Date: 2026-09-26 16:33:28.470161

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9f3924adeea"
down_revision: str | Sequence[str] | None = "c2bfb7a6c2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # `solved=NULL` now means "topic encountered, outcome unknown" (e.g. a
    # hint request) -- distinct from `False` ("observed as unsolved").
    op.alter_column("learning_events", "solved", existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Collapse the "unknown outcome" state back into `False` so the NOT NULL
    # constraint can be restored without failing on existing NULL rows.
    op.execute("UPDATE learning_events SET solved = false WHERE solved IS NULL")
    op.alter_column("learning_events", "solved", existing_type=sa.Boolean(), nullable=False)
