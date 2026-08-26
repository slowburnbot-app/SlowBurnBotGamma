"""Follow-seed queries shared by the dashboard and bot routers.

A seed's performance is derived, not stored: FollowTarget.source embeds the
seed handle ("<handle>[followers]", "<handle>[similar]", "<handle>[likers]"),
so grouping follow_targets by source and mapping the prefix back to the handle
gives each seed its all-time follow-back numbers.
"""
import uuid
from collections import defaultdict

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
        "total": s["total"],
        "complete": s["complete"],
        "followed_back": s["followed_back"],
        "rate": round(s["followed_back"] / s["complete"], 2) if s["complete"] else None,
    }


async def list_seeds(session: AsyncSession, account_id: uuid.UUID, active_only: bool = False) -> dict:
    query = select(FollowSeed).where(FollowSeed.account_id == account_id)
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
