"""add follow candidate filters to user_configs

Revision ID: y5x0y1z2a3b4
Revises: x4w9x0y1z2a3
Create Date: 2026-08-26

"""
from alembic import op
import sqlalchemy as sa

revision = 'y5x0y1z2a3b4'
down_revision = 'x4w9x0y1z2a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0 disables a rule. Defaults: skip accounts over 5000 followers, require
    # following >= 50% of followers, require at least 1 post.
    op.add_column(
        'user_configs',
        sa.Column('max_followers', sa.Integer(), nullable=False, server_default='5000'),
    )
    op.add_column(
        'user_configs',
        sa.Column('min_follow_ratio_pct', sa.Integer(), nullable=False, server_default='50'),
    )
    op.add_column(
        'user_configs',
        sa.Column('min_posts', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('user_configs', 'min_posts')
    op.drop_column('user_configs', 'min_follow_ratio_pct')
    op.drop_column('user_configs', 'max_followers')
