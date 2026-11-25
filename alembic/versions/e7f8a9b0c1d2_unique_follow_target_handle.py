"""follow_targets: one row per (account, handle), case-insensitive

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-11-25

Duplicates double-counted the source statistics of the seed pool (20 groups, 32
extra rows on 2026-11-25; each group had the same status and the same follow_back).
This revision keeps the oldest row of each group, then adds the unique index. The
bot endpoint that creates targets answers a duplicate with the existing row.
The downgrade drops the index only: the removed duplicates are not restored.
"""
from alembic import op

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM follow_targets t
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY account_id, lower(target_handle) ORDER BY created_at, id
            ) AS rn
            FROM follow_targets
        ) d
        WHERE t.id = d.id AND d.rn > 1
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_follow_targets_account_handle ON follow_targets (account_id, lower(target_handle))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_follow_targets_account_handle")
