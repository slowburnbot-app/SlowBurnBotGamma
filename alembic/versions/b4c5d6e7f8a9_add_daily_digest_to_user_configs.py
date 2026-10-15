"""add user_configs.timezone and daily_digest_sent_on

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-10-15

The "daily" session notification type sends one digest email per user per
day. `timezone` is the IANA name saved from the dashboard; `daily_digest_sent_on`
is the local date of the last digest, so the hourly check sends it once.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b4c5d6e7f8a9'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_configs', sa.Column('timezone', sa.String(64), nullable=True))
    op.add_column('user_configs', sa.Column('daily_digest_sent_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_configs', 'daily_digest_sent_on')
    op.drop_column('user_configs', 'timezone')
