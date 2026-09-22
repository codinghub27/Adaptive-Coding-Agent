"""initial empty

Revision ID: 0bfa97139592
Revises:
Create Date: 2026-09-23 00:05:14.000515

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0bfa97139592"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
