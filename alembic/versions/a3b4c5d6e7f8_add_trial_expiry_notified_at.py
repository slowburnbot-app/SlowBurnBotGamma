"""add subscriptions.trial_expiry_notified_at

Revision ID: a3b4c5d6e7f8
Revises: f2e7f8a9b0c1
Create Date: 2026-09-16

The backend sends the admin one email when an invite trial ends. This column
records when that email went out, so the check does not send it again.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3b4c5d6e7f8'
down_revision = 'f2e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'subscriptions',
        sa.Column('trial_expiry_notified_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('subscriptions', 'trial_expiry_notified_at')
