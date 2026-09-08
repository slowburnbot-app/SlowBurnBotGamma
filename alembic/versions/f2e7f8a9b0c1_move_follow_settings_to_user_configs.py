"""move unfollow_days and the follow candidate filters to user_configs

Revision ID: f2e7f8a9b0c1
Revises: e1d6e7f8a9b0
Create Date: 2026-09-08

The unfollow window and the hover-card filters (max_followers /
min_follow_ratio_pct / min_posts) become user-wide: they move from
account_settings to user_configs and the dashboard edits them on /dashboard/config.
Each user keeps the values from the account settings row that was updated most
recently. A user with no account settings row keeps the defaults (30 / 5000 / 50 / 1).
"""
from alembic import op
import sqlalchemy as sa

revision = 'f2e7f8a9b0c1'
down_revision = 'e1d6e7f8a9b0'
branch_labels = None
depends_on = None

COLUMNS = (
    ('unfollow_days', '30'),
    ('max_followers', '5000'),
    ('min_follow_ratio_pct', '50'),
    ('min_posts', '1'),
)
COL_LIST = ', '.join(name for name, _ in COLUMNS)


def _copy(src_table: str, dst_table: str) -> None:
    """Copy the 4 columns from the most recently updated src row per user into dst."""
    assignments = ', '.join(f'{name} = s.{name}' for name, _ in COLUMNS)
    op.execute(
        f"UPDATE {dst_table} d SET {assignments} "
        f"FROM (SELECT DISTINCT ON (user_id) user_id, {COL_LIST} FROM {src_table} "
        f"ORDER BY user_id, updated_at DESC NULLS LAST) s "
        f"WHERE s.user_id = d.user_id"
    )


def upgrade() -> None:
    for name, default in COLUMNS:
        op.add_column('user_configs', sa.Column(name, sa.Integer(), nullable=False, server_default=default))
    _copy('account_settings', 'user_configs')
    for name, _ in COLUMNS:
        op.drop_column('account_settings', name)


def downgrade() -> None:
    for name, default in COLUMNS:
        op.add_column('account_settings', sa.Column(name, sa.Integer(), nullable=False, server_default=default))
    # Every account of a user gets the user-wide value back.
    assignments = ', '.join(f'{name} = u.{name}' for name, _ in COLUMNS)
    op.execute(
        f"UPDATE account_settings a SET {assignments} "
        f"FROM user_configs u WHERE u.user_id = a.user_id"
    )
    for name, _ in COLUMNS:
        op.drop_column('user_configs', name)
