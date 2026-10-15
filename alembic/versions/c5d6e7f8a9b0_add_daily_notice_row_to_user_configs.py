"""add the Daily notification row to user_configs

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-15

Daily is its own notification row (checkbox, type, email, phone), like Login/Error.
It replaces the short-lived Session type value "daily": rows that saved it become
Daily on, Session off, Session type email.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_configs', sa.Column('notices_daily', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('user_configs', sa.Column('daily_notices_type', sa.String(10), nullable=False, server_default='email'))
    op.add_column('user_configs', sa.Column('daily_notify_email', sa.String(255), nullable=True))
    op.add_column('user_configs', sa.Column('daily_notify_phone', sa.String(30), nullable=True))
    op.execute(
        "UPDATE user_configs SET notices_daily = true, notices_session = false, notices_type = 'email' "
        "WHERE notices_type = 'daily'"
    )


def downgrade() -> None:
    op.drop_column('user_configs', 'daily_notify_phone')
    op.drop_column('user_configs', 'daily_notify_email')
    op.drop_column('user_configs', 'daily_notices_type')
    op.drop_column('user_configs', 'notices_daily')
