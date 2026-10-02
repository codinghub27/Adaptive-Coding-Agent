"""active problem on conversations, fixed ladder ceiling

Revision ID: d1e2f3a4b5c6
Revises: 0ba22af5050e
Create Date: 2026-10-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "0ba22af5050e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ADAPTIVE-upgrade P1: a conversation remembers its active problem, so
    # follow-ups resolve to it (F1/F2/B3). All nullable: existing rows simply
    # have no active problem yet.
    op.add_column(
        "conversations",
        sa.Column("active_problem", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("conversations", sa.Column("active_problem_key", sa.String(32), nullable=True))
    op.add_column("conversations", sa.Column("active_topic", sa.String(64), nullable=True))
    # Fixed per-ladder ceiling (F3). NULL keeps the old per-turn behaviour.
    op.add_column("hint_progress", sa.Column("ceiling", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("hint_progress", "ceiling")
    op.drop_column("conversations", "active_topic")
    op.drop_column("conversations", "active_problem_key")
    op.drop_column("conversations", "active_problem")
