"""Helpers for syncing Stripe Subscription objects into our DB.

Centralizes the int-timestamp → tz-aware datetime conversion and the
Stripe price-id → plan tier mapping so webhook and admin resync paths
stay consistent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.user import User
from app.plan_tiers import is_valid_tier
from app.services.plan_enforcement import enforce_account_limits
from app.settings import settings

logger = logging.getLogger(__name__)

# Stripe statuses that mean "no longer entitled." A subscription's line items
# still resolve to a paid tier after cancellation (Stripe doesn't strip
# `items` from a deleted subscription object), so these are handled
# separately from the price->tier mapping below rather than relying on it.
TERMINAL_STATUSES = {"canceled", "unpaid", "incomplete_expired"}


def stripe_ts_to_datetime(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _price_to_tier_map() -> dict[str, str]:
    return {
        settings.stripe_price_crawl: "crawl",
        settings.stripe_price_walk: "walk",
        settings.stripe_price_run: "run",
    }


def price_id_for_tier(tier: str) -> str | None:
    """Inverse of _price_to_tier_map — the configured Stripe price id for a
    plan tier, or None if that tier has no price configured."""
    for price_id, mapped_tier in _price_to_tier_map().items():
        if mapped_tier == tier and price_id:
            return price_id
    return None


def _subscription_items(stripe_sub) -> list:
    """The subscription's line items list.

    Deliberately dict-style (`stripe_sub["items"]`), not attribute access
    (`stripe_sub.items`) — Stripe SDK objects are dict-like, so `.items` on
    one resolves to Python's built-in `dict.items` bound method rather than
    ever reaching the actual Stripe "items" field via __getattr__. That
    collision silently returned "no items" for every subscription here,
    which is why price->tier mapping via webhook has never worked.
    """
    items = stripe_sub.get("items") if hasattr(stripe_sub, "get") else None
    data = items.get("data") if items else None
    return data or []


def _tier_from_stripe_sub(stripe_sub) -> str | None:
    mapping = _price_to_tier_map()
    for item in _subscription_items(stripe_sub):
        price = getattr(item, "price", None)
        price_id = getattr(price, "id", None) if price else None
        if price_id and price_id in mapping and mapping[price_id]:
            return mapping[price_id]
    return None


def _current_period(stripe_sub) -> tuple[int | None, int | None]:
    """(current_period_start, current_period_end) as unix timestamps.

    Newer Stripe API versions (this account is on 2026-08-26.dahlia) dropped
    these off the top-level Subscription object — each subscription item now
    carries its own period, since a subscription can mix items with
    different billing cycles. We only ever create single-item subscriptions
    (one price per plan tier), so the first item's period is authoritative.
    Older API versions still set the top-level fields, so check those first
    for backward compatibility.
    """
    start = getattr(stripe_sub, "current_period_start", None)
    end = getattr(stripe_sub, "current_period_end", None)
    if start and end:
        return start, end

    items = _subscription_items(stripe_sub)
    if items:
        first = items[0]
        return getattr(first, "current_period_start", None), getattr(first, "current_period_end", None)
    return None, None


async def apply_stripe_subscription(
    session: AsyncSession, stripe_sub, is_deletion: bool = False
) -> Subscription | None:
    """Upsert a Subscription row from a Stripe subscription object.

    Locates the row by stripe_subscription_id, then by stripe_customer_id.
    Updates status and period dates. When the incoming status is terminal
    (canceled/unpaid/incomplete_expired) the user is forced back to the
    "free" tier regardless of what the line items still say — a canceled
    subscription object still carries its old price/tier, so trusting the
    price mapping here would leave the account fully entitled after
    cancellation. Otherwise, when the price maps cleanly to a tier, both
    Subscription.plan_tier and User.plan_tier are updated. Either branch
    re-enforces account limits. Caller is responsible for committing.

    `is_deletion` should be set for `customer.subscription.deleted` events —
    it's used only to let that event itself land even though the guard below
    would otherwise treat "already terminal" as a reason to ignore it.
    """
    result = await session.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_sub.id
        )
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        result = await session.execute(
            select(Subscription).where(
                Subscription.stripe_customer_id == stripe_sub.customer
            )
        )
        sub = result.scalar_one_or_none()

    if sub is None:
        logger.warning("Stripe sync: no subscription row for %s", stripe_sub.id)
        return None

    incoming_status = stripe_sub.status

    # Guard against out-of-order webhook delivery: once this row is recorded
    # as terminally canceled, ignore a stale non-deletion event (e.g. a
    # delayed/retried `customer.subscription.updated` with status=active)
    # that would resurrect entitlement with no active payment. A genuine
    # resubscribe arrives as a *new* Stripe subscription id (Stripe does not
    # revive a canceled subscription object), so this only ever suppresses
    # stale replays for the same id.
    if (
        not is_deletion
        and sub.status in TERMINAL_STATUSES
        and incoming_status not in TERMINAL_STATUSES
        and stripe_sub.id == sub.stripe_subscription_id
    ):
        logger.info(
            "Stripe sync: ignoring stale %s event for already-canceled subscription %s",
            incoming_status, stripe_sub.id,
        )
        return sub

    sub.stripe_subscription_id = stripe_sub.id
    sub.stripe_customer_id = stripe_sub.customer
    sub.status = incoming_status
    period_start, period_end = _current_period(stripe_sub)
    sub.current_period_start = stripe_ts_to_datetime(period_start)
    sub.current_period_end = stripe_ts_to_datetime(period_end)

    if incoming_status in TERMINAL_STATUSES:
        # sub.plan_tier is left untouched (mirrors admin.py's deactivate) so a
        # future reactivation can fall back to the prior tier; only the
        # user's *effective* tier is forced to free for enforcement.
        user_result = await session.execute(select(User).where(User.id == sub.user_id))
        user = user_result.scalar_one_or_none()
        if user is not None:
            user.plan_tier = "free"
        await enforce_account_limits(sub.user_id, session)
        return sub

    tier = _tier_from_stripe_sub(stripe_sub)
    if tier and is_valid_tier(tier):
        sub.plan_tier = tier
        user_result = await session.execute(select(User).where(User.id == sub.user_id))
        user = user_result.scalar_one_or_none()
        if user is not None:
            user.plan_tier = tier
        await enforce_account_limits(sub.user_id, session)
    else:
        # Price id didn't map to a known tier (env drift / new Stripe price /
        # misconfigured settings). Leaving plan_tier untouched while status
        # goes active would strand a paying customer at whatever tier they
        # had before (often "free" = 0 accounts) with no visibility into why.
        logger.warning(
            "Stripe sync: subscription %s is %s but its price(s) did not map "
            "to a known plan tier — plan_tier left unchanged. Check "
            "stripe_price_crawl/walk/run settings.",
            stripe_sub.id, incoming_status,
        )

    return sub
