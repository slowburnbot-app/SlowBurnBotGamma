"""Instagram action-limit cooldowns.

The bot client reports each detected limit (see bot-client/burnBot_actionLimit.py).
This module owns the policy:

  hard tier   -> cooldown of 24h on the first strike, 48h on the second and 72h
                 from the third on, counting hard rows for the same (account,
                 action) in the last 7 days. The bot skips that action while
                 `until` is ahead.
  soft / status_page -> history only (no cooldown).
  status page clean  -> a 48h/72h cooldown that has already run >= 24h is
                 shortened to exactly 24h. Never below 24h: Instagram's Account
                 Status page is not known to show short rate limits, so a clean
                 read cannot prove the block is over.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.action_limit import ActionLimit
from app.schemas.action_limit import ActiveActionLimit

STRIKE_WINDOW = timedelta(days=7)
BASE_COOLDOWN = timedelta(hours=24)
_COOLDOWN_BY_STRIKE = {1: timedelta(hours=24), 2: timedelta(hours=48)}
_MAX_COOLDOWN = timedelta(hours=72)


def cooldown_for_strike(strike: int) -> timedelta:
    return _COOLDOWN_BY_STRIKE.get(strike, _MAX_COOLDOWN)


async def record_action_limit(
    session: AsyncSession,
    account: Account,
    action: str,
    tier: str,
    reason: str,
    details: str | None,
) -> ActionLimit:
    """Insert one event row; hard-tier rows get a strike count and `until`."""
    now = datetime.now(timezone.utc)
    strike = 1
    until = None
    if tier == "hard":
        result = await session.execute(
            select(ActionLimit.id).where(
                ActionLimit.account_id == account.id,
                ActionLimit.action == action,
                ActionLimit.tier == "hard",
                ActionLimit.created_at >= now - STRIKE_WINDOW,
            )
        )
        strike = len(result.all()) + 1
        until = now + cooldown_for_strike(strike)
    row = ActionLimit(
        user_id=account.user_id,
        account_id=account.id,
        action=action,
        tier=tier,
        reason=(reason or "")[:200],
        strike=strike,
        until=until,
        details=details,
        created_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def active_limits(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ActiveActionLimit]]:
    """Newest still-running hard cooldown per (account, action), one query."""
    out: dict[uuid.UUID, list[ActiveActionLimit]] = {aid: [] for aid in account_ids}
    if not account_ids:
        return out
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ActionLimit)
        .where(
            ActionLimit.account_id.in_(account_ids),
            ActionLimit.tier == "hard",
            ActionLimit.until.is_not(None),
            ActionLimit.until > now,
            ActionLimit.cleared_at.is_(None),
        )
        .order_by(ActionLimit.created_at.desc())
    )
    seen: set[tuple[uuid.UUID, str]] = set()
    for row in result.scalars().all():
        key = (row.account_id, row.action)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(row.account_id, []).append(ActiveActionLimit.model_validate(row))
    for aid in out:
        out[aid].sort(key=lambda x: x.action)
    return out


async def record_status_page_check(
    session: AsyncSession, account: Account, clean: bool | None
) -> list[ActionLimit]:
    """Stamp the account's status-page read (clean / flagged / unreadable);
    on a clean read shorten any active repeat cooldown to 24h. Returns the
    rows that were shortened."""
    now = datetime.now(timezone.utc)
    account.status_page_checked_at = now
    account.status_page_result = "unreadable" if clean is None else ("clean" if clean else "flagged")
    released: list[ActionLimit] = []
    if clean:
        result = await session.execute(
            select(ActionLimit).where(
                ActionLimit.account_id == account.id,
                ActionLimit.tier == "hard",
                ActionLimit.until.is_not(None),
                ActionLimit.until > now,
                ActionLimit.cleared_at.is_(None),
            )
        )
        for row in result.scalars().all():
            floor = row.created_at + BASE_COOLDOWN
            if row.until > floor and now >= floor:
                row.until = floor
                row.cleared_at = now
                row.cleared_reason = "status page clean"
                released.append(row)
    await session.commit()
    for row in released:
        await session.refresh(row)
    return released
