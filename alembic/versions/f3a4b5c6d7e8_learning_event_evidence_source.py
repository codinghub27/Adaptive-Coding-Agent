"""learning event evidence source

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ADAPTIVE-upgrade P5: provenance of each event's outcome evidence
    # (extracted | synthesised | none). Historical rows did not record it;
    # "none" is the honest default for them only where solved IS NULL, so the
    # backfill below leaves outcome rows as 'unknown'.
    op.add_column(
        "learning_events",
        sa.Column("evidence_source", sa.String(16), nullable=False, server_default="none"),
    )
    op.execute(
        "UPDATE learning_events SET evidence_source = 'unknown' WHERE solved IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("learning_events", "evidence_source")
