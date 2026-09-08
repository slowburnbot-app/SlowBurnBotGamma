"""add themes table (admin-managed base24 catalog)

Revision ID: e1d6e7f8a9b0
Revises: d0c5d6e7f8a9
Create Date: 2026-09-08

Until now the set of themes a user could pick on /dashboard/account was
whatever YAML files shipped in frontend/themes/ — adding one meant a deploy.
The catalog moves here so an admin can curate it at /admin/themes (add from
the bundled base24 gallery or a pasted scheme, delete). The three schemes
that existed as files are seeded verbatim so nobody's stored choice breaks.

`slowburnbot` stays special: frontend/themes/slowburnbot.yaml is still read
server-side as the before-paint default, the row here is what "reset to
default" fetches, and the API refuses to delete it. Keep the two in sync.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e1d6e7f8a9b0'
down_revision = 'd0c5d6e7f8a9'
branch_labels = None
depends_on = None


SEED = [
    {
        "name": "Slow Burn Bot",
        "slug": "slowburnbot",
        "variant": "dark",
        "author": "SlowBurnBot",
        "palette": {
            "base00": "#141413", "base01": "#1c1b1a", "base02": "#302f2c", "base03": "#3d3d3a",
            "base04": "#9a968b", "base05": "#f4f3ee", "base06": "#faf9f4", "base07": "#ffffff",
            "base08": "#e87755", "base09": "#b58fbf", "base0A": "#e5c07b", "base0B": "#a0b87c",
            "base0C": "#7aaa96", "base0D": "#d97757", "base0E": "#8aa5c4", "base0F": "#a8765a",
            "base10": "#0d0c0c", "base11": "#2e2d2b", "base12": "#cf3b0a", "base13": "#f1d49a",
            "base14": "#bdd498", "base15": "#a0ccbc", "base16": "#aec2dc", "base17": "#cdadd4",
        },
    },
    {
        "name": "Catppuccin Frappe",
        "slug": "catppuccin-frappe",
        "variant": "dark",
        "author": "https://github.com/catppuccin/catppuccin",
        "palette": {
            "base00": "#303446", "base01": "#292c3c", "base02": "#414559", "base03": "#51576d",
            "base04": "#626880", "base05": "#c6d0f5", "base06": "#f2d5cf", "base07": "#babbf1",
            "base08": "#e78284", "base09": "#ef9f76", "base0A": "#e5c890", "base0B": "#a6d189",
            "base0C": "#81c8be", "base0D": "#8caaee", "base0E": "#ca9ee6", "base0F": "#eebebe",
            "base10": "#292c3c", "base11": "#232634", "base12": "#ea999c", "base13": "#f2d5cf",
            "base14": "#a6d189", "base15": "#99d1db", "base16": "#85c1dc", "base17": "#f4b8e4",
        },
    },
    {
        "name": "Tokyo Night Storm",
        "slug": "tokyo-night-storm",
        "variant": "dark",
        "author": "Michaël Ball, based on Tokyo Night by enkia (https://github.com/enkia/tokyo-night-vscode-theme)",
        "palette": {
            "base00": "#24283b", "base01": "#16161e", "base02": "#343a52", "base03": "#444b6a",
            "base04": "#787c99", "base05": "#a9b1d6", "base06": "#cbccd1", "base07": "#d5d6db",
            "base08": "#c0caf5", "base09": "#a9b1d6", "base0A": "#0db9d7", "base0B": "#9ece6a",
            "base0C": "#b4f9f8", "base0D": "#2ac3de", "base0E": "#bb9af7", "base0F": "#f7768e",
            "base10": "#1f2335", "base11": "#1a1b26", "base12": "#ff7a93", "base13": "#ff9e64",
            "base14": "#73daca", "base15": "#7dcfff", "base16": "#89ddff", "base17": "#bb9af7",
        },
    },
]


def upgrade() -> None:
    themes = op.create_table(
        'themes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('variant', sa.String(5), nullable=False),  # dark | light
        sa.Column('author', sa.String(300), nullable=True),
        sa.Column('palette', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_themes_slug', 'themes', ['slug'], unique=True)

    import uuid
    op.bulk_insert(themes, [{"id": uuid.uuid4(), **row} for row in SEED])


def downgrade() -> None:
    op.drop_index('ix_themes_slug', table_name='themes')
    op.drop_table('themes')
