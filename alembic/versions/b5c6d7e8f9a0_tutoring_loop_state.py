"""tutoring loop state: pending check, session progress, concept grade, error last-seen

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema (ADAPTIVE-tutoring Q1/Q4/Q5: additive only)."""
    op.add_column(
        "conversations",
        sa.Column("pending_check", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("session_progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("learning_events", sa.Column("concept_grade", sa.String(16), nullable=True))
    op.add_column(
        "learner_profiles",
        sa.Column(
            "common_errors_seen",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("learner_profiles", "common_errors_seen")
    op.drop_column("learning_events", "concept_grade")
    op.drop_column("conversations", "session_progress")
    op.drop_column("conversations", "pending_check")
