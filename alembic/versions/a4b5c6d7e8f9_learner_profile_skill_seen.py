"""learner profile skill seen

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ADAPTIVE-upgrade P6: when each skill key last had an observed outcome
    # (read-time decay, "current focus"). Empty for existing profiles, i.e.
    # no decay is applied to evidence whose age is unknown.
    op.add_column(
        "learner_profiles",
        sa.Column(
            "skill_seen",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Backfill from the event log (the source of truth): each topic's last
    # OBSERVED outcome. Without it every existing learner's focus went blank.
    op.execute(
        """
        UPDATE learner_profiles p SET skill_seen = s.seen
        FROM (
            SELECT user_id, jsonb_object_agg(topic, last_at) AS seen
            FROM (
                SELECT user_id, topic, to_char(max(created_at) AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') AS last_at
                FROM learning_events WHERE solved IS NOT NULL
                GROUP BY user_id, topic
            ) t GROUP BY user_id
        ) s
        WHERE p.user_id = s.user_id
        """
    )
    # F10: drop stored skill keys that no event of that learner ever had as
    # its topic -- the LLM-proposed `pattern` keys ("Union find" on a trees
    # conversation) that P6 stops writing.
    op.execute(
        """
        UPDATE learner_profiles p SET skill_levels = COALESCE((
            SELECT jsonb_object_agg(k.key, k.value)
            FROM jsonb_each(p.skill_levels) k
            WHERE EXISTS (
                SELECT 1 FROM learning_events e
                WHERE e.user_id = p.user_id AND e.topic = k.key
            )
        ), '{}'::jsonb)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("learner_profiles", "skill_seen")
