"""Tell the admin when an invite trial ends.

The backend has no scheduler. app/main.py runs `notify_expired_trials` in a
background task: once at startup, then once per hour. Entitlement itself is
checked lazily in app/deps.py; this module only sends the email and records
that it was sent, so each trial produces one message.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session_maker
from app.models.subscription import Subscription
from app.models.user import User
from app.services.admin_notify import notify_admin

logger = logging.getLogger(__name__)

TRIAL_EXPIRY_CHECK_SECONDS = 60 * 60


async def notify_expired_trials() -> int:
    """Email the admin for each trial that ended and was not reported yet.

    Returns the number of emails attempted."""
    now = datetime.now(timezone.utc)
    count = 0
    async with async_session_maker() as session:
        result = await session.execute(
            select(Subscription, User.email)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == "trialing",
                Subscription.current_period_end.is_not(None),
                Subscription.current_period_end < now,
                Subscription.trial_expiry_notified_at.is_(None),
            )
        )
        for sub, email in result.all():
            ended = sub.current_period_end
            await notify_admin(
                "invite trial expired",
                [
                    "An invite trial ended. The account no longer has access.",
                    "",
                    f"email:        {email}",
                    f"plan tier:    {sub.plan_tier}",
                    f"trial ended:  {ended.strftime('%Y-%m-%d %H:%M UTC') if ended else '----'}",
                    "",
                    "Review at /admin/users.",
                ],
                session,
            )
            sub.trial_expiry_notified_at = now
            count += 1
        if count:
            await session.commit()
    if count:
        logger.info("Trial expiry check: %d notification(s) sent", count)
    return count
