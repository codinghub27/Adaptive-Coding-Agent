"""hint progress verified attempt

Revision ID: 0ba22af5050e
Revises: a9f3924adeea
Create Date: 2026-09-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ba22af5050e"
down_revision: str | Sequence[str] | None = "a9f3924adeea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Tracks whether a sandbox has ever produced a pass/fail `Verdict` (never
    # inconclusive/skipped) for this (user, conversation, topic)'s problem --
    # the "demonstrated effort" input to the Packet P3 assistance-escalation
    # rule in `app.agents.planner.build_plan`. Backfilled `false` for existing
    # rows: no history of a real run is the safe default (never grants
    # escalation retroactively).
    op.add_column(
        "hint_progress",
        sa.Column(
            "has_verified_attempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("hint_progress", "has_verified_attempt")
