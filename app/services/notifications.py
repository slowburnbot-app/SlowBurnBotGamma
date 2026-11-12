"""Server-side notification dispatch for the bot client.

The bot client posts events to /bot/notify and this module reads Resend and
TextBelt credentials from `system_configs` and sends them. Credentials
never leave the backend.
"""
import logging

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt
from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Raised when a notification cannot be sent (config missing or transport failed)."""


async def _load_system_config(session: AsyncSession) -> SystemConfig | None:
    result = await session.execute(select(SystemConfig))
    return result.scalar_one_or_none()


def _decrypt_secret(ciphertext: str, label: str) -> str:
    """Decrypt a stored credential, or raise NotificationError with a clear cause.

    InvalidToken means the value was encrypted under a different key than the one
    the app uses now (for example CREDENTIAL_ENCRYPTION_KEY was set without
    migrating system_configs). Without this wrapper the bot gets a bare 500.
    """
    try:
        return decrypt(ciphertext)
    except InvalidToken as e:
        logger.error("%s cannot be decrypted with the current key; re-enter it in /admin/config", label)
        raise NotificationError(
            f"{label} cannot be decrypted with the current encryption key; re-enter it in /admin/config"
        ) from e


async def send_email(
    to: str,
    subject: str,
    body: str,
    session: AsyncSession,
    html: str | None = None,
) -> None:
    """Send an email through Resend. `body` is the plain text; `html` is an optional
    HTML version that clients show in place of the text."""
    config = await _load_system_config(session)
    if config is None or not config.resend_api_key_enc or not config.resend_from_address:
        raise NotificationError("Resend not configured")
    api_key = _decrypt_secret(config.resend_api_key_enc, "Resend API key")

    try:
        payload: dict = {
            "from": config.resend_from_address,
            "to": [to],
            "subject": subject or "SlowBurnBot",
            "text": body,
        }
        if html:
            payload["html"] = html
        if config.resend_reply_to:
            payload["reply_to"] = config.resend_reply_to
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        if resp.status_code not in (200, 201):
            logger.error("Resend rejected send: %s %s", resp.status_code, resp.text)
            raise NotificationError(f"Resend rejected send: {resp.status_code}")
    except NotificationError:
        raise
    except Exception as e:
        logger.error("Resend request failed: %s", e)
        raise NotificationError(f"Resend request failed: {e}") from e


def _to_e164(phone: str) -> str:
    """Normalize a stored phone number to E.164 before handing it to TextBelt.

    `notify_phone`/`login_notify_phone` are saved as bare US digits by the
    frontend (see dashboard/config's stripPhone), so TextBelt sees e.g.
    "5551234567". TextBelt now rejects anything that isn't E.164.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if phone.startswith("+"):
        return phone
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    raise NotificationError(f"Phone number is not in a recognizable format: {phone!r}")


async def send_sms(to: str, body: str, session: AsyncSession) -> None:
    config = await _load_system_config(session)
    if config is None or not config.textbelt_key_enc:
        raise NotificationError("TextBelt key not configured")
    api_key = _decrypt_secret(config.textbelt_key_enc, "TextBelt key")
    if not api_key:
        raise NotificationError("TextBelt key not configured")

    to = _to_e164(to)

    # TextBelt caps messages at ~160 chars; trim to avoid silent truncation.
    if len(body) > 160:
        body = body[:157] + "..."

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://textbelt.com/text",
                data={"phone": to, "message": body, "key": api_key},
            )
        result = resp.json()
    except Exception as e:
        raise NotificationError(f"TextBelt request failed: {e}") from e

    if not result.get("success"):
        raise NotificationError(f"TextBelt rejected send: {result.get('error') or result}")
