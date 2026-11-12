"""Send one summary email per day to users whose session notices are set to "daily".

The backend has no scheduler. app/main.py runs `send_daily_digests` in a
background task every DIGEST_CHECK_SECONDS. A user gets the digest on the first
check at or after DIGEST_HOUR (9 PM) in their timezone. `daily_digest_sent_on`
holds the local date of the last digest, so each day produces one message.
The digest is built from `session_logs`; the bot client uploads nothing extra.
"""
import logging
import re
from datetime import date, datetime, timezone, tzinfo
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import async_session_maker
from app.models.account import Account
from app.models.session_log import SessionLog
from app.models.user_config import UserConfig
from app.services.notifications import NotificationError, send_email, send_sms

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


_ISSUE_TAG = re.compile(r"\[(?:error|warning)\]\s*", re.IGNORECASE)


def _issue_lines(text: str | None) -> list[str]:
    """Split a newline-joined error or warning log into lines.

    The bot writes one issue per line ("like[topics]: [warning] feed stalled").
    The digest labels each block as errors or warnings, so the "[error]" or
    "[warning]" tag is removed from each line.
    """
    lines = []
    for raw in (text or "").splitlines():
        line = _ISSUE_TAG.sub("", raw.strip(), count=1).strip()
        if line:
            lines.append(line)
    return lines


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _by_account(rows: list[tuple[str, SessionLog]]) -> dict[str, list[SessionLog]]:
    by_account: dict[str, list[SessionLog]] = {}
    for name, log in rows:
        by_account.setdefault(name, []).append(log)
    return by_account


def build_digest_body(day: date, rows: list[tuple[str, SessionLog]], tz: tzinfo) -> str:
    """Plain-text digest. `rows` is (account name, session log), ordered by name then start time.

    Mail clients that cannot show HTML show this text (see build_digest_html).
    """
    by_account = _by_account(rows)
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
            lines += [f"      ERROR: {line}" for line in _issue_lines(log.error_message)]
            lines += [f"      WARN: {line}" for line in _issue_lines(log.warning_message)]
    return "\n".join(lines)


# HTML digest. Email clients ignore most <style> rules, so every style is inline
# and the layout uses tables. All text from the database goes through escape().
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_PAGE = "#f4f3ee"
_CARD = "#ffffff"
_INK = "#141413"
_MUTED = "#6f6b62"
_LINE = "#e4e2da"
_CHIP = "#efede6"
_ACCENT = "#d97757"
_ERR_INK, _ERR_BG, _ERR_BAR = "#a3300b", "#fbeae4", "#cf3b0a"
_WARN_INK, _WARN_BG, _WARN_BAR = "#6e5212", "#fbf3e0", "#e5c07b"

_TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def _stat_html(value: int, word: str, color: str) -> str:
    label = word if value == 1 else f"{word}s"
    return (
        f'<td align="center" style="padding:12px 2px;">'
        f'<div style="font-size:22px;font-weight:700;line-height:1.2;color:{color};">{value}</div>'
        f'<div style="font-size:11px;color:{_MUTED};text-transform:uppercase;">'
        f"{label}</div></td>"
    )


def _issue_box_html(label: str, lines: list[str], ink: str, bg: str, bar: str) -> str:
    """One box for the errors (or the warnings) of a session.

    The label ("ERROR:") is on the same line as the first issue. The lines after
    it align with the text of the first issue. The label cell has width="1%" so
    that it is only as wide as the label.
    """
    rows = "".join(
        f'<tr><td width="1%" valign="top" style="padding:2px 8px 2px 0;font-size:12px;'
        f'font-weight:700;line-height:19px;white-space:nowrap;color:{ink};">'
        f'{label + ":" if i == 0 else ""}</td>'
        f'<td valign="top" style="padding:2px 0;font-size:13px;line-height:19px;'
        f'word-break:break-word;color:{ink};">{escape(line)}</td></tr>'
        for i, line in enumerate(lines)
    )
    return (
        f'<tr><td colspan="2" style="padding:6px 0 2px;">'
        f'<div style="background:{bg};border-left:3px solid {bar};border-radius:4px;'
        f'padding:6px 12px;"><table {_TABLE}>{rows}</table></div></td></tr>'
    )


def _session_html(log: SessionLog, tz: tzinfo, first: bool) -> str:
    border = "" if first else f"border-top:1px solid {_LINE};"
    parts = [
        f'<table {_TABLE} style="{border}">',
        f'<tr><td colspan="2" style="padding:12px 0 6px;font-size:14px;color:{_INK};">'
        f'<span style="display:inline-block;padding:1px 7px;border-radius:9px;background:{_CHIP};'
        f'color:{_MUTED};font-size:12px;font-weight:600;">#{log.run_sequence}</span>&nbsp; '
        f"<strong>{_fmt_time(log.start_time, tz)} &ndash; {_fmt_time(log.end_time, tz)}</strong>"
        f'<span style="color:{_MUTED};">&nbsp;&middot;&nbsp;'
        f"{_fmt_duration(log.start_time, log.end_time)}</span></td></tr>",
    ]
    slots = _slots(log)
    if slots:
        for action, count in slots:
            parts.append(
                f'<tr><td width="40" align="right" valign="top" style="padding:2px 12px 2px 0;'
                f'font-size:14px;font-weight:700;color:{_INK};">{count}</td>'
                f'<td valign="top" style="padding:2px 0;font-size:14px;'
                f'color:{_INK};word-break:break-word;">{escape(action)}</td></tr>'
            )
    else:
        parts.append(
            f'<tr><td colspan="2" style="padding:2px 0;font-size:13px;font-style:italic;'
            f'color:{_MUTED};">No actions</td></tr>'
        )
    errors = _issue_lines(log.error_message)
    if errors:
        parts.append(_issue_box_html("ERROR", errors, _ERR_INK, _ERR_BG, _ERR_BAR))
    warnings = _issue_lines(log.warning_message)
    if warnings:
        parts.append(_issue_box_html("WARNING", warnings, _WARN_INK, _WARN_BG, _WARN_BAR))
    parts.append('<tr><td colspan="2" style="padding:0 0 10px;"></td></tr></table>')
    return "".join(parts)


def build_digest_html(day: date, rows: list[tuple[str, SessionLog]], tz: tzinfo) -> str:
    """HTML digest with the same content as build_digest_body, for clients that show HTML."""
    by_account = _by_account(rows)
    total_actions = sum(c for _, log in rows for _, c in _slots(log))
    errors = sum(1 for _, log in rows if _issue_lines(log.error_message))
    warnings = sum(1 for _, log in rows if _issue_lines(log.warning_message))
    day_label = f"{day:%A, %B} {day.day}, {day.year}"

    stats = "".join([
        _stat_html(len(by_account), "account", _INK),
        _stat_html(len(rows), "session", _INK),
        _stat_html(total_actions, "action", _INK),
        _stat_html(errors, "error", _ERR_BAR if errors else _MUTED),
        _stat_html(warnings, "warning", _WARN_INK if warnings else _MUTED),
    ])

    cards = []
    for name, logs in by_account.items():
        acct_actions = sum(c for log in logs for _, c in _slots(log))
        sessions = "".join(_session_html(log, tz, i == 0) for i, log in enumerate(logs))
        cards.append(
            f'<table {_TABLE} style="background:{_CARD};border:1px solid {_LINE};border-radius:8px;'
            f'margin:0 0 16px;border-collapse:separate;">'
            f'<tr><td style="padding:14px 18px;border-bottom:1px solid {_LINE};">'
            f"<table {_TABLE}><tr>"
            f'<td style="font-size:16px;font-weight:700;color:{_INK};word-break:break-word;">'
            f"{escape(name)}</td>"
            f'<td align="right" style="font-size:13px;color:{_MUTED};white-space:nowrap;'
            f'padding-left:12px;">{_plural(len(logs), "session")} &middot; '
            f'{_plural(acct_actions, "action")}</td>'
            f"</tr></table></td></tr>"
            f'<tr><td style="padding:0 18px 4px;">{sessions}</td></tr></table>'
        )

    title = f"SlowBurnBot daily summary - {day.isoformat()}"
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{_PAGE};">'
        f'<table {_TABLE} style="background:{_PAGE};"><tr><td align="center" style="padding:24px 12px;">'
        f'<table {_TABLE} style="max-width:640px;font-family:{_FONT};color:{_INK};">'
        # Header band
        f'<tr><td style="background:{_INK};border-radius:8px 8px 0 0;padding:18px 20px;">'
        f'<div style="font-size:13px;font-weight:700;color:{_ACCENT};letter-spacing:0.06em;'
        f'text-transform:uppercase;">SlowBurnBot</div>'
        f'<div style="font-size:20px;font-weight:700;color:#f4f3ee;padding-top:2px;">Daily summary</div>'
        f'<div style="font-size:14px;color:#c9c6bb;padding-top:2px;">{day_label}</div>'
        f"</td></tr>"
        # Totals
        f'<tr><td style="background:{_CARD};border:1px solid {_LINE};border-top:0;'
        f'border-radius:0 0 8px 8px;">'
        f"<table {_TABLE}><tr>{stats}</tr></table></td></tr>"
        f'<tr><td style="padding:16px 0 0;">{"".join(cards)}</td></tr>'
        f"</table></td></tr></table></body></html>"
    )


def build_digest_sms(day: date, rows: list[tuple[str, SessionLog]]) -> str:
    """One-line digest for a text message (send_sms trims to 160 characters)."""
    accounts = len({name for name, _ in rows})
    actions = sum(c for _, log in rows for _, c in _slots(log))
    errors = sum(1 for _, log in rows if log.error_message)
    warnings = sum(1 for _, log in rows if log.warning_message)
    text = f"SlowBurnBot {day.strftime('%m-%d')}: {accounts} accts, {len(rows)} sessions, {actions} actions"
    if errors:
        text += f", {errors} error(s)"
    if warnings:
        text += f", {warnings} warning(s)"
    return text


async def send_daily_digests(now: datetime | None = None) -> int:
    """Send the digest to each user who is due. Returns the number of emails sent."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    async with async_session_maker() as session:
        configs = (
            await session.execute(
                select(UserConfig).where(UserConfig.notices_daily.is_(True))
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
                pairs = [(n, log) for n, log in rows]
                channel = (cfg.daily_notices_type or "email").strip().lower()
                email = cfg.daily_notify_email or cfg.notify_email
                phone = cfg.daily_notify_phone or cfg.notify_phone
                delivered = False
                attempted = False
                if channel in ("email", "both") and email:
                    attempted = True
                    try:
                        await send_email(
                            to=email,
                            subject=f"SlowBurnBot daily summary - {today.isoformat()}",
                            body=build_digest_body(today, pairs, tz),
                            html=build_digest_html(today, pairs, tz),
                            session=session,
                        )
                        delivered = True
                    except NotificationError as e:
                        logger.warning("Daily digest email failed for user %s: %s", cfg.user_id, e)
                if channel in ("text", "both") and phone:
                    attempted = True
                    try:
                        await send_sms(to=phone, body=build_digest_sms(today, pairs), session=session)
                        delivered = True
                    except NotificationError as e:
                        logger.warning("Daily digest text failed for user %s: %s", cfg.user_id, e)
                if not delivered:
                    # Date stays unset, so the next check tries again (a failed send, or a
                    # destination the user adds before midnight).
                    if not attempted:
                        logger.info("Daily digest: user %s has no destination for %r", cfg.user_id, channel)
                    continue
                sent += 1

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
