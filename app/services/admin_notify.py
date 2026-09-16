"""Best-effort email notifications to the admin address.

The address comes from SystemConfig.admin_notify_email, with
resend_reply_to as the fallback. A missing address or a transport failure
is logged and never raised, so the caller's request is not affected.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_config import SystemConfig
from app.services.notifications import NotificationError, send_email

logger = logging.getLogger(__name__)


async def notify_admin(subject: str, lines: list[str], session: AsyncSession) -> None:
    config = await session.scalar(select(SystemConfig))
    to = None
    if config is not None:
        to = config.admin_notify_email or config.resend_reply_to
    if not to:
        logger.warning("Admin notification skipped (no admin notify address): %s", subject)
        return
    try:
        await send_email(
            to=to,
            subject=f"SlowBurnBot — {subject}",
            body="\n".join(lines),
            session=session,
        )
    except NotificationError as e:
        logger.error("Admin notification failed (%s): %s", subject, e)
    except Exception as e:  # never let a notification break the caller
        logger.error("Admin notification crashed (%s): %s", subject, e)
