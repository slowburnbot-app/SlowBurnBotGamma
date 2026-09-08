"""Unauthenticated endpoints backing the public landing page.

Anything here is reachable by anyone on the internet (via the Next.js proxy),
so: no echo of input, no enumeration of existing users, hard size caps at the
schema layer. Per-IP rate limiting lives in the Next.js route handler
(frontend/app/api/account-request/route.ts) — the proxy strips x-forwarded-*
before requests reach this process, so the real client IP is only visible there.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.account_request import AccountRequest
from app.models.system_config import SystemConfig
from app.services.notifications import NotificationError, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])


class AccountRequestBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=320)
    company: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    account_count: int | None = Field(default=None, ge=1, le=500)
    instagram_handles: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)
    # Honeypot — hidden on the real form, so anything filling it is a bot.
    website: str | None = Field(default=None, max_length=500)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _notification_body(req: AccountRequest) -> str:
    lines = [
        "New account request from the SlowBurnBot landing page.",
        "",
        f"name:      {req.name}",
        f"email:     {req.email}",
        f"company:   {req.company or '----'}",
        f"industry:  {req.industry or '----'}",
        f"accounts:  {req.account_count if req.account_count is not None else '----'}",
        "",
        "instagram handles:",
        req.instagram_handles or "----",
        "",
        "notes:",
        req.notes or "----",
        "",
        "Review at /admin/requests.",
    ]
    return "\n".join(lines)


@router.post("/account-request")
async def submit_account_request(
    body: AccountRequestBody,
    session: AsyncSession = Depends(get_async_session),
):
    """Store a prospect's request and announce it to the admin. Anonymous.

    Always answers {"ok": true} on a well-formed body — including when the
    honeypot tripped (nothing stored, nothing sent) and when the notification
    email fails (row is already committed; the email is best-effort).
    """
    if _clean(body.website):
        logger.info("Account request honeypot tripped; dropping submission")
        return {"ok": True}

    req = AccountRequest(
        name=body.name.strip(),
        email=str(body.email).strip().lower(),
        company=_clean(body.company),
        industry=_clean(body.industry),
        account_count=body.account_count,
        instagram_handles=_clean(body.instagram_handles),
        notes=_clean(body.notes),
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)

    config = await session.scalar(select(SystemConfig))
    to = None
    if config is not None:
        to = config.admin_notify_email or config.resend_reply_to
    if not to:
        logger.warning("Account request %s stored but no admin notify address is set", req.id)
        return {"ok": True}

    try:
        await send_email(
            to=to,
            subject=f"SlowBurnBot — account request from {req.name}",
            body=_notification_body(req),
            session=session,
        )
    except NotificationError as e:
        logger.error("Account request %s stored but notification failed: %s", req.id, e)

    return {"ok": True}
