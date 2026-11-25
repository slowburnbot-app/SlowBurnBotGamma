"""follow_seeds: cool-down, strike counter, last action

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-11-25

An exhausted seed (saturation) or a seed with a soft failure (no posts twice) no
longer retires for good: it gets a cool-down and the server brings it back when
the time ends. `strikes` counts soft failures in a row. `last_action` is the
action (followers, following, similar, likers) that wrote `last_saturation`, so
a saturation value of one action never combines with another action.
`removed_at` is the tombstone of a seed that the user removed: the row stays (so
the bot does not discover or import the handle again) and the dashboard hides it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('follow_seeds', sa.Column('cooldown_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('follow_seeds', sa.Column('strikes', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('follow_seeds', sa.Column('last_action', sa.String(30), nullable=True))
    op.add_column('follow_seeds', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('follow_seeds', 'removed_at')
    op.drop_column('follow_seeds', 'last_action')
    op.drop_column('follow_seeds', 'strikes')
    op.drop_column('follow_seeds', 'cooldown_until')
