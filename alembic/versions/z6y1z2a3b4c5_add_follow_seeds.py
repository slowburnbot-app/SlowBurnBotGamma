"""add follow_seeds table + account_settings.account_group_mode

Revision ID: z6y1z2a3b4c5
Revises: y5x0y1z2a3b4
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'z6y1z2a3b4c5'
down_revision = 'y5x0y1z2a3b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'follow_seeds',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('handle', sa.String(150), nullable=False),
        sa.Column('origin', sa.String(200), nullable=False, server_default='manual'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retire_reason', sa.String(200), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_saturation', sa.Integer(), nullable=True),
        sa.UniqueConstraint('account_id', 'handle', name='uq_follow_seeds_account_handle'),
    )
    op.create_index('ix_follow_seeds_user_id', 'follow_seeds', ['user_id'])
    op.create_index('ix_follow_seeds_account_id', 'follow_seeds', ['account_id'])

    # Per-account switch: "account group" means the manual text (default, unchanged
    # behaviour) or the dynamic seed pool. The pool starts empty and is bootstrapped
    # by the bot from the account group's Similar accounts — nothing is copied here.
    op.add_column(
        'account_settings',
        sa.Column('account_group_mode', sa.String(10), nullable=False, server_default='manual'),
    )


def downgrade() -> None:
    op.drop_column('account_settings', 'account_group_mode')
    op.drop_index('ix_follow_seeds_account_id', table_name='follow_seeds')
    op.drop_index('ix_follow_seeds_user_id', table_name='follow_seeds')
    op.drop_table('follow_seeds')
