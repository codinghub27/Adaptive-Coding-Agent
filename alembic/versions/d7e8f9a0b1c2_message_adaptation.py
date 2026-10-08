"""messages.adaptation: what changed in a reply because of the learner

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-10-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema (additive only: one nullable column).

    Existing rows keep NULL, which reads as "nothing was adapted": the badge
    on stored history used to be shown for every reply whether or not anything
    had changed, and there is no record to recover the truth from.
    """
    op.add_column(
        "messages",
        sa.Column("adaptation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "adaptation")
