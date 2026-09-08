"""add account_requests table + system_configs.admin_notify_email

Revision ID: d0c5d6e7f8a9
Revises: c9b4c5d6e7f8
Create Date: 2026-09-08

Registration is invite-only and invites are admin-issued, so until now a
prospect had no way to raise their hand. The public landing page at / gains a
"request an account" form; each submission lands here as one row, and the
backend announces it by email to `admin_notify_email` (falling back to the
Resend reply-to address). The row is the durable record — the email is
best-effort and never blocks the submission.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd0c5d6e7f8a9'
down_revision = 'c9b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'account_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('company', sa.String(200), nullable=True),
        sa.Column('industry', sa.String(200), nullable=True),
        sa.Column('account_count', sa.Integer(), nullable=True),
        sa.Column('instagram_handles', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='new'),  # new | contacted | invited | declined
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_account_requests_email', 'account_requests', ['email'])

    op.add_column('system_configs', sa.Column('admin_notify_email', sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column('system_configs', 'admin_notify_email')
    op.drop_index('ix_account_requests_email', table_name='account_requests')
    op.drop_table('account_requests')
