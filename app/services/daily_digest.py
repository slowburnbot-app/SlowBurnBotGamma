"""Send one summary email per day to users whose session notices are set to "daily".

The backend has no scheduler. app/main.py runs `send_daily_digests` in a
background task every DIGEST_CHECK_SECONDS. A user gets the digest on the first
check at or after DIGEST_HOUR (9 PM) in their timezone. `daily_digest_sent_on`
holds the local date of the last digest, so each day produces one message.
The digest is built from `session_logs`; the bot client uploads nothing extra.
"""
import logging
from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import async_session_maker
from app.models.account import Account
from app.models.session_log import SessionLog
from app.models.user_config import UserConfig
from app.services.notifications import NotificationError, send_email

logger = logging.getLogger(__name__)

DIGEST_HOUR = 21
DIGEST_CHECK_SECONDS = 15 * 60


def _fmt_time(dt: datetime | None, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%I:%M %p") if dt else "----"


def _fmt_duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "--:--"
    secs = max(int((end - start).total_seconds()), 0)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _slots(log: SessionLog) -> list[tuple[str, int]]:
    pairs = [
        (log.action_1_type, log.action_1_count),
        (log.action_2_type, log.action_2_count),
        (log.action_3_type, log.action_3_count),
        (log.action_4_type, log.action_4_count),
    ]
    return [(t, c or 0) for t, c in pairs if t]


def build_digest_body(day: date, rows: list[tuple[str, SessionLog]], tz: tzinfo) -> str:
    """Plain-text digest. `rows` is (account name, session log), ordered by name then start time."""
    by_account: dict[str, list[SessionLog]] = {}
    for name, log in rows:
        by_account.setdefault(name, []).append(log)

    total_actions = sum(c for _, log in rows for _, c in _slots(log))
    lines = [
        f"SlowBurnBot daily summary - {day.isoformat()}",
        f"{len(by_account)} account(s), {len(rows)} session(s), {total_actions} action(s)",
    ]
    for name, logs in by_account.items():
        acct_actions = sum(c for log in logs for _, c in _slots(log))
        lines += ["", f"{name} - {len(logs)} session(s), {acct_actions} action(s)"]
        for log in logs:
            lines.append(
                f"  [{log.run_sequence}] {_fmt_time(log.start_time, tz)} - "
                f"{_fmt_time(log.end_time, tz)} [{_fmt_duration(log.start_time, log.end_time)}]"
            )
            slots = _slots(log)
            if slots:
                lines += [f"      [{c:02d}] {t}" for t, c in slots]
            else:
                lines.append("      no actions")
            if log.error_message:
                lines.append(f"      ERROR: {log.error_message.strip()}")
            if log.warning_message:
                lines.append(f"      WARN: {log.warning_message.strip()}")
    return "\n".join(lines)


async def send_daily_digests(now: datetime | None = None) -> int:
    """Send the digest to each user who is due. Returns the number of emails sent."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    async with async_session_maker() as session:
        configs = (
            await session.execute(
                select(UserConfig).where(
                    UserConfig.notices_session.is_(True),
                    UserConfig.notices_type == "daily",
                    UserConfig.notify_email.is_not(None),
                    UserConfig.notify_email != "",
                )
            )
        ).scalars().all()

        for cfg in configs:
            tz = await _user_tz(session, cfg)
            local_now = now.astimezone(tz)
            today = local_now.date()
            if local_now.hour < DIGEST_HOUR or cfg.daily_digest_sent_on == today:
                continue

            rows = (
                await session.execute(
                    select(Account.name, SessionLog)
                    .join(Account, Account.id == SessionLog.account_id)
                    .where(SessionLog.user_id == cfg.user_id, SessionLog.run_date == today)
                    .order_by(Account.name, SessionLog.start_time)
                )
            ).all()

            if rows:
                try:
                    await send_email(
                        to=cfg.notify_email,
                        subject=f"SlowBurnBot daily summary - {today.isoformat()}",
                        body=build_digest_body(today, [(n, log) for n, log in rows], tz),
                        session=session,
                    )
                    sent += 1
                except NotificationError as e:
                    # Leave the date unset so the next check tries again.
                    logger.warning("Daily digest failed for user %s: %s", cfg.user_id, e)
                    continue

            # No sessions today: nothing to send, but mark the day so the check stops.
            cfg.daily_digest_sent_on = today
            await session.commit()
    if sent:
        logger.info("Daily digest: %d email(s) sent", sent)
    return sent


async def _user_tz(session, cfg: UserConfig) -> tzinfo:
    """Saved IANA timezone, else the offset of the newest session, else UTC."""
    if cfg.timezone:
        try:
            return ZoneInfo(cfg.timezone)
        except Exception:
            logger.warning("Bad timezone %r for user %s", cfg.timezone, cfg.user_id)
    start = (
        await session.execute(
            select(SessionLog.start_time)
            .where(SessionLog.user_id == cfg.user_id, SessionLog.start_time.is_not(None))
            .order_by(SessionLog.start_time.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if start is not None and start.utcoffset() is not None:
        return timezone(start.utcoffset())
    return timezone.utc
