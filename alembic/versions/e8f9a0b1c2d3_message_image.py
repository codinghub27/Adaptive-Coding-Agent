"""messages.image: the image shared with a message, and what was read from it

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-10-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema (additive only: three nullable columns).

    A shared image used to live only in the browser tab that sent it: after a
    reload the message came back as "[image]" with nothing to show, and the
    tutor's own history of the conversation said "[image]" too. Existing rows
    keep NULL: those images were never sent to be stored.
    """
    op.add_column("messages", sa.Column("image", sa.LargeBinary(), nullable=True))
    op.add_column("messages", sa.Column("image_mime", sa.String(length=64), nullable=True))
    op.add_column("messages", sa.Column("image_text", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "image_text")
    op.drop_column("messages", "image_mime")
    op.drop_column("messages", "image")
