"""rename "post engagers" action targets to "likers" in account_settings.actions

Revision ID: b8a3b4c5d6e7
Revises: a7z2a3b4c5d6
Create Date: 2026-09-03

One concept had five spellings across surfaces; everything standardizes on
"likers" (the word follow_targets.source and the log labels already use). The
UI now emits "account list [likers]" / "topics [likers]"; this rewrites the
stored settings targets to match. Bot clients >= 1.195 accept both old and new
spellings, so no compatibility window is needed for updated clients.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'b8a3b4c5d6e7'
down_revision = 'a7z2a3b4c5d6'
branch_labels = None
depends_on = None

_RENAMES = {
    "account list [post engagers]": "account list [likers]",
    "post engagers [account list]": "account list [likers]",
    "topics [post engagers]": "topics [likers]",
    "post engagers [topics]": "topics [likers]",
}

_UPDATE = sa.text(
    "UPDATE account_settings SET actions = :actions WHERE id = :id"
).bindparams(sa.bindparam("actions", type_=JSONB))


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, actions FROM account_settings WHERE actions IS NOT NULL")
    ).fetchall()
    for row_id, actions in rows:
        if not isinstance(actions, list):
            continue
        changed = False
        for action in actions:
            if isinstance(action, dict) and action.get("target") in _RENAMES:
                action["target"] = _RENAMES[action["target"]]
                changed = True
        if changed:
            conn.execute(_UPDATE, {"actions": actions, "id": row_id})


def downgrade() -> None:
    # No-op: the old spellings stay accepted by the bot client, and reversing
    # the rewrite would only reintroduce the inconsistency this fixes.
    pass
