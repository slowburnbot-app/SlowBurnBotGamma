"""move follow candidate filters from user_configs to account_settings

Revision ID: a7z2a3b4c5d6
Revises: z6y1z2a3b4c5
Create Date: 2026-08-26

Different accounts want different thresholds (a brand vs. a local venue), so the
hover-card filters become per-account. Values are not migrated: the columns had
only existed for a few hours at their defaults (5000 / 50 / 1), which are the
same defaults applied here.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7z2a3b4c5d6'
down_revision = 'z6y1z2a3b4c5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('account_settings', sa.Column('max_followers', sa.Integer(), nullable=False, server_default='5000'))
    op.add_column('account_settings', sa.Column('min_follow_ratio_pct', sa.Integer(), nullable=False, server_default='50'))
    op.add_column('account_settings', sa.Column('min_posts', sa.Integer(), nullable=False, server_default='1'))
    op.drop_column('user_configs', 'min_posts')
    op.drop_column('user_configs', 'min_follow_ratio_pct')
    op.drop_column('user_configs', 'max_followers')


def downgrade() -> None:
    op.add_column('user_configs', sa.Column('max_followers', sa.Integer(), nullable=False, server_default='5000'))
    op.add_column('user_configs', sa.Column('min_follow_ratio_pct', sa.Integer(), nullable=False, server_default='50'))
    op.add_column('user_configs', sa.Column('min_posts', sa.Integer(), nullable=False, server_default='1'))
    op.drop_column('account_settings', 'min_posts')
    op.drop_column('account_settings', 'min_follow_ratio_pct')
    op.drop_column('account_settings', 'max_followers')
