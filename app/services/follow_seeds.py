"""Follow-seed queries shared by the dashboard and bot routers.

A seed's performance is derived, not stored: FollowTarget.source embeds the
seed handle ("<handle>[followers]", "<handle>[similar]", "<handle>[likers]"),
so grouping follow_targets by source and mapping the prefix back to the handle
gives each seed its all-time follow-back numbers.
"""
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.follow_seed import FollowSeed
from app.models.follow_target import FollowTarget


def _source_prefix(source: str | None) -> str | None:
    """'nike[followers]' -> 'nike'. Keeps a leading '#': '#nike[likers]' is a
    topic source and must not share a bucket with the seed account 'nike'."""
    if not source:
        return None
    head = source.split("[", 1)[0].strip().lower()
    return head or None


async def seed_stats_by_handle(session: AsyncSession, account_id: uuid.UUID) -> tuple[dict[str, dict], float | None]:
    """{handle: {total, complete, followed_back}} plus the account-wide rate."""
    result = await session.execute(
        select(
            FollowTarget.source,
            func.count().label("total"),
            func.count().filter(FollowTarget.follow_back.isnot(None)).label("complete"),
            func.count().filter(FollowTarget.follow_back == True).label("followed_back"),  # noqa: E712
        )
        .where(FollowTarget.account_id == account_id)
        .group_by(FollowTarget.source)
    )
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "complete": 0, "followed_back": 0})
    all_complete = 0
    all_fb = 0
    for row in result.all():
        all_complete += row.complete
        all_fb += row.followed_back
        handle = _source_prefix(row.source)
        if handle is None:
            continue
        s = stats[handle]
        s["total"] += row.total
        s["complete"] += row.complete
        s["followed_back"] += row.followed_back
    account_rate = round(all_fb / all_complete, 2) if all_complete else None
    return stats, account_rate


def seed_to_dict(seed: FollowSeed, stats: dict[str, dict]) -> dict:
    s = stats.get(seed.handle, {"total": 0, "complete": 0, "followed_back": 0})
    return {
        "id": seed.id,
        "handle": seed.handle,
        "origin": seed.origin,
        "active": seed.active,
        "added_at": seed.added_at,
        "retired_at": seed.retired_at,
        "retire_reason": seed.retire_reason,
        "last_used_at": seed.last_used_at,
        "last_saturation": seed.last_saturation,
        "last_action": seed.last_action,
        "strikes": seed.strikes or 0,
        "cooldown_until": seed.cooldown_until,
        "removed_at": seed.removed_at,
        "total": s["total"],
        "complete": s["complete"],
        "followed_back": s["followed_back"],
        "rate": round(s["followed_back"] / s["complete"], 2) if s["complete"] else None,
    }


def reactivate_if_cooled(seed: FollowSeed, now: datetime) -> bool:
    """Bring a seed back when its cool-down has ended. Returns True when it changed.
    Only a seed with a cool-down comes back: a permanent retirement (private profile,
    not found, low follow-back, disabled by the user) has no cooldown_until."""
    if seed.active or seed.removed_at is not None or seed.cooldown_until is None or seed.cooldown_until > now:
        return False
    seed.active = True
    seed.retired_at = None
    seed.retire_reason = None
    seed.cooldown_until = None
    seed.last_saturation = None
    seed.last_action = None
    seed.strikes = 0
    return True


def reset_seed_state(seed: FollowSeed) -> None:
    """A user re-enables a seed: forget the old strikes, saturation and cool-down, so the
    seed does not come back one strike from retirement."""
    seed.active = True
    seed.retired_at = None
    seed.retire_reason = None
    seed.cooldown_until = None
    seed.removed_at = None
    seed.last_saturation = None
    seed.last_action = None
    seed.strikes = 0


def remove_seed(seed: FollowSeed, now: datetime) -> None:
    """The user removes a seed: keep the row as a tombstone (inactive, hidden, no way back)."""
    seed.active = False
    seed.retired_at = now
    seed.retire_reason = "removed by user"
    seed.cooldown_until = None
    seed.removed_at = now


async def reactivate_cooled_seeds(session: AsyncSession, account_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(FollowSeed).where(
            FollowSeed.account_id == account_id,
            FollowSeed.active == False,  # noqa: E712
            FollowSeed.cooldown_until.isnot(None),
            FollowSeed.cooldown_until <= now,
        )
    )
    changed = sum(1 for s in result.scalars().all() if reactivate_if_cooled(s, now))
    if changed:
        await session.commit()
    return changed


async def list_seeds(session: AsyncSession, account_id: uuid.UUID, active_only: bool = False,
                     include_removed: bool = False) -> dict:
    """include_removed: the bot needs the tombstones (so it never re-adds a removed handle);
    the dashboard does not show them."""
    query = select(FollowSeed).where(FollowSeed.account_id == account_id)
    if not include_removed:
        query = query.where(FollowSeed.removed_at.is_(None))
    if active_only:
        query = query.where(FollowSeed.active == True)  # noqa: E712
    query = query.order_by(FollowSeed.active.desc(), FollowSeed.added_at.asc())
    seeds = (await session.execute(query)).scalars().all()
    stats, account_rate = await seed_stats_by_handle(session, account_id)
    items = [seed_to_dict(s, stats) for s in seeds]
    return {
        "items": items,
        "active_count": sum(1 for s in seeds if s.active),
        "account_rate": account_rate,
    }


def normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@").lower()
