"""add action_limits table + accounts.status_page_checked_at / status_page_result

Revision ID: c9b4c5d6e7f8
Revises: b8a3b4c5d6e7
Create Date: 2026-09-05

Instagram throttles likes/follows per account with no advance warning. The bot
client (>= 1.198) now detects the block dialog, rejected requests and
flip-then-revert UI, and reports each event here. One row per event keeps the
history the 7-day strike escalation (24h / 48h / 72h) is computed from; the
active cooldown for (account, action) is the newest row with `until` still
ahead and `cleared_at` unset. The two account columns record the once-a-day
read of Instagram's own Account Status pages, which can shorten a repeat
cooldown back to 24h when the account reads clean.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c9b4c5d6e7f8'
down_revision = 'b8a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'action_limits',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),          # like | follow | unfollow | all
        sa.Column('tier', sa.String(20), nullable=False),            # soft | hard | status_page
        sa.Column('reason', sa.String(200), nullable=False, server_default=''),
        sa.Column('strike', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cleared_reason', sa.String(200), nullable=True),
    )
    op.create_index('ix_action_limits_user_id', 'action_limits', ['user_id'])
    op.create_index('ix_action_limits_account_id', 'action_limits', ['account_id'])

    op.add_column('accounts', sa.Column('status_page_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('accounts', sa.Column('status_page_result', sa.String(20), nullable=True))  # clean | flagged


def downgrade() -> None:
    op.drop_column('accounts', 'status_page_result')
    op.drop_column('accounts', 'status_page_checked_at')
    op.drop_index('ix_action_limits_account_id', table_name='action_limits')
    op.drop_index('ix_action_limits_user_id', table_name='action_limits')
    op.drop_table('action_limits')
