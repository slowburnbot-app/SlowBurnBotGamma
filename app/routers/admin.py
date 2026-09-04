"""Admin-only endpoints."""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_superuser
from app.crypto import decrypt, encrypt
from app.database import get_async_session
from app.models.account import Account
from app.services import github_actions, object_storage
from app.models.follow_target import FollowTarget
from app.models.invite_code import InviteCode
from app.models.subscription import Subscription
from app.models.system_config import SystemConfig
from app.models.user import User
from app.plan_tiers import is_valid_tier
from app.schemas.admin import NotificationCredentialsRead, NotificationCredentialsUpdate
from app.services.email import send_invite_email
from app.services.plan_enforcement import enforce_account_limits
from app.services.stripe_sync import apply_stripe_subscription

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[dict])
async def list_users(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(User).options(selectinload(User.subscription))
    )
    users = result.scalars().all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "plan_tier": u.plan_tier,
            "is_active": u.is_active,
            "subscription_status": u.subscription.status if u.subscription else "none",
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@router.post("/users/{user_id}/sync-subscription")
async def sync_subscription(
    user_id: uuid.UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Manually re-sync a user's subscription status from Stripe."""
    import stripe as stripe_lib
    from app.settings import settings

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured.")

    stripe_lib.api_key = settings.stripe_secret_key

    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None or not sub.stripe_subscription_id:
        raise HTTPException(status_code=404, detail="No Stripe subscription on record.")

    stripe_sub = stripe_lib.Subscription.retrieve(sub.stripe_subscription_id)
    await apply_stripe_subscription(session, stripe_sub)
    await session.commit()
    return {"status": sub.status, "plan_tier": sub.plan_tier}


class ActivateSubscriptionRequest(BaseModel):
    plan_tier: str | None = None
    # When set, grants a timed trial instead of a plain activation — mirrors
    # the invite-code trial mechanism (status="trialing", current_period_end
    # = now + trial_days) for an account that's already registered, e.g. a
    # demo account that later needs "reactivate as a trial" rather than
    # "activate with no end date."
    trial_days: int | None = None


@router.post("/users/{user_id}/activate")
async def activate_subscription(
    user_id: uuid.UUID,
    body: ActivateSubscriptionRequest | None = None,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Admin-activate a user's subscription. Tier comes from the request body
    if provided; otherwise the existing Subscription.plan_tier is used. Either
    way the resulting tier must be a valid PLAN_TIERS key.

    With trial_days set, status becomes "trialing" with current_period_end
    pushed out that many days, instead of "active" with no period end."""
    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="No subscription record found.")

    requested_tier = body.plan_tier if body else None
    target_tier = requested_tier or sub.plan_tier
    if not is_valid_tier(target_tier):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot activate: tier '{target_tier}' is not valid. Set a tier first.",
        )

    trial_days = body.trial_days if body else None
    if trial_days is not None:
        if trial_days <= 0:
            raise HTTPException(status_code=400, detail="trial_days must be positive.")
        sub.status = "trialing"
        sub.current_period_end = datetime.now(timezone.utc) + timedelta(days=trial_days)
    else:
        sub.status = "active"
    sub.plan_tier = target_tier

    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user:
        user.plan_tier = target_tier

    await enforce_account_limits(user_id, session)
    await session.commit()
    return {
        "status": sub.status,
        "plan_tier": sub.plan_tier,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


@router.post("/users/{user_id}/deactivate")
async def deactivate_subscription(
    user_id: uuid.UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Admin-deactivate a user's subscription. Status flips to inactive but
    plan_tier is preserved so reactivation can use the prior tier."""
    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="No subscription record found.")
    sub.status = "inactive"

    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user:
        user.plan_tier = "free"

    await enforce_account_limits(user_id, session)
    await session.commit()
    return {"status": sub.status, "plan_tier": sub.plan_tier}


class SetTierRequest(BaseModel):
    plan_tier: str


@router.post("/users/{user_id}/set-tier")
async def set_user_tier(
    user_id: uuid.UUID,
    body: SetTierRequest,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Set a user's subscription tier and enforce account limits."""
    if not is_valid_tier(body.plan_tier):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.plan_tier}")

    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.plan_tier = body.plan_tier

    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="No subscription record found.")
    sub.plan_tier = body.plan_tier
    sub.status = "active"

    await enforce_account_limits(user_id, session)
    await session.commit()
    return {"plan_tier": sub.plan_tier, "status": sub.status}


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_account(
    account_id: uuid.UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete any user's account (cascading)."""
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    await session.delete(account)
    await session.commit()


@router.get("/accounts", response_model=list[dict])
async def list_all_accounts(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(Account, User.email)
        .join(User, Account.user_id == User.id)
        .order_by(User.email, Account.name)
    )
    rows = result.all()
    return [
        {
            "id": str(a.id),
            "user_id": str(a.user_id),
            "user_email": email,
            "name": a.name,
            "enabled": a.enabled,
            "system_disabled": a.system_disabled,
            "group_number": a.group_number,
            "created_at": a.created_at.isoformat(),
        }
        for a, email in rows
    ]


@router.get("/accounts/{account_id}/follow-targets", response_model=dict)
async def list_follow_targets(
    account_id: uuid.UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    offset = (page - 1) * page_size

    total_result = await session.execute(
        select(func.count()).where(FollowTarget.account_id == account_id)
    )
    total = total_result.scalar_one()

    result = await session.execute(
        select(FollowTarget)
        .where(FollowTarget.account_id == account_id)
        .order_by(FollowTarget.follow_date.desc().nullslast(), FollowTarget.target_handle)
        .offset(offset)
        .limit(page_size)
    )
    targets = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(t.id),
                "target_handle": t.target_handle,
                "source": t.source,
                "status": t.status,
                "follow_date": t.follow_date.isoformat() if t.follow_date else None,
                "unfollow_date": t.unfollow_date.isoformat() if t.unfollow_date else None,
                "follow_back": t.follow_back,
            }
            for t in targets
        ],
    }


async def _get_system_config(session: AsyncSession) -> SystemConfig:
    """Get the singleton SystemConfig row, creating it if missing."""
    result = await session.execute(select(SystemConfig))
    config = result.scalar_one_or_none()
    if config is None:
        config = SystemConfig()
        session.add(config)
        await session.commit()
        await session.refresh(config)
    return config


@router.get("/notification-credentials", response_model=NotificationCredentialsRead)
async def get_notification_credentials(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Read SMTP/TextBelt config. Secrets are masked (set/not set)."""
    config = await _get_system_config(session)
    return NotificationCredentialsRead(
        smtp_server=config.smtp_server or "",
        smtp_port=config.smtp_port or 587,
        smtp_user=config.smtp_user,
        smtp_password_set=config.smtp_password_enc is not None,
        textbelt_key_set=config.textbelt_key_enc is not None,
        resend_api_key_set=config.resend_api_key_enc is not None,
        resend_from_address=config.resend_from_address,
        resend_reply_to=config.resend_reply_to,
        updated_at=config.updated_at,
    )


@router.put("/notification-credentials", response_model=NotificationCredentialsRead)
async def update_notification_credentials(
    body: NotificationCredentialsUpdate,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Update SMTP/TextBelt/Resend config. Only provided fields are changed."""
    config = await _get_system_config(session)

    if body.smtp_server is not None:
        config.smtp_server = body.smtp_server
    if body.smtp_port is not None:
        config.smtp_port = body.smtp_port
    if body.smtp_user is not None:
        config.smtp_user = body.smtp_user
    if body.smtp_password is not None:
        config.smtp_password_enc = encrypt(body.smtp_password) if body.smtp_password else None
    if body.textbelt_key is not None:
        config.textbelt_key_enc = encrypt(body.textbelt_key) if body.textbelt_key else None
    if body.resend_api_key is not None:
        config.resend_api_key_enc = encrypt(body.resend_api_key) if body.resend_api_key else None
    if body.resend_from_address is not None:
        config.resend_from_address = body.resend_from_address or None
    if body.resend_reply_to is not None:
        config.resend_reply_to = body.resend_reply_to or None

    await session.commit()
    await session.refresh(config)

    return NotificationCredentialsRead(
        smtp_server=config.smtp_server or "",
        smtp_port=config.smtp_port or 587,
        smtp_user=config.smtp_user,
        smtp_password_set=config.smtp_password_enc is not None,
        textbelt_key_set=config.textbelt_key_enc is not None,
        resend_api_key_set=config.resend_api_key_enc is not None,
        resend_from_address=config.resend_from_address,
        resend_reply_to=config.resend_reply_to,
        updated_at=config.updated_at,
    )


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------

class InviteCreateRequest(BaseModel):
    email: str | None = None
    free_trial_days: int | None = None
    plan_tier: str = "crawl"
    send_email: bool = False


def _invite_dict(inv: InviteCode) -> dict:
    return {
        "id": str(inv.id),
        "code": inv.code,
        "email": inv.email,
        "free_trial_days": inv.free_trial_days,
        "plan_tier": inv.plan_tier,
        "used_by_user_id": str(inv.used_by_user_id) if inv.used_by_user_id else None,
        "used_at": inv.used_at.isoformat() if inv.used_at else None,
        "created_at": inv.created_at.isoformat(),
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
    }


@router.post("/invites")
async def create_invite(
    body: InviteCreateRequest,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    if body.plan_tier and not is_valid_tier(body.plan_tier):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.plan_tier}")

    code = secrets.token_urlsafe(6)[:8].upper()
    invite = InviteCode(
        code=code,
        email=body.email,
        free_trial_days=body.free_trial_days,
        plan_tier=body.plan_tier,
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)

    if body.send_email and body.email:
        try:
            await send_invite_email(body.email, code, body.free_trial_days, session)
        except Exception as e:
            return {**_invite_dict(invite), "email_error": str(e)}

    return _invite_dict(invite)


@router.get("/invites")
async def list_invites(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(InviteCode).order_by(InviteCode.created_at.desc())
    )
    return [_invite_dict(inv) for inv in result.scalars().all()]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invite(
    invite_id: uuid.UUID,
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(InviteCode).where(InviteCode.id == invite_id))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found.")
    if invite.used_by_user_id is not None:
        raise HTTPException(status_code=400, detail="Cannot revoke an already-used invite.")
    await session.delete(invite)
    await session.commit()


# ---------------------------------------------------------------------------
# Bot version management
# ---------------------------------------------------------------------------

@router.get("/bot-version")
async def get_bot_version_status(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Return current DB bot version and what GitHub's main branch currently reports."""
    config = await _get_system_config(session)
    github_version = None
    github_error = None
    try:
        github_version = await github_actions.get_main_branch_bot_version()
    except Exception as e:
        github_error = str(e)
    return {
        "db_current_bot_version": config.current_bot_version,
        "db_current_bot_release_date": config.current_bot_release_date,
        "github_main_version": github_version,
        "github_error": github_error,
    }


@router.post("/sync-bot-version")
async def sync_bot_version(
    _: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Advance current_bot_version once both build artifacts (EXE + Docker image) exist.

    Returns 200 when the DB is updated, 202 while the build is still running.
    """
    try:
        version = await github_actions.get_main_branch_bot_version()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")
    if not version:
        raise HTTPException(status_code=502, detail="Could not read BOT_VERSION from GitHub main branch.")

    exe_key = f"releases/windows/SlowBurnBot-{version}.exe"
    # Transient failures while checking artifact readiness (S3 timeouts, GHCR
    # blips) mean "not ready yet", not a server error — return 202 so the
    # release script keeps polling instead of aborting on a 500.
    try:
        exe_ready = object_storage.object_exists(exe_key)
    except Exception:
        exe_ready = False
    try:
        image_ready = await github_actions.ghcr_image_has_tag(version)
    except Exception:
        image_ready = False

    if not (exe_ready and image_ready):
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending",
                "target_version": version,
                "exe_ready": exe_ready,
                "image_ready": image_ready,
            },
        )

    config = await _get_system_config(session)
    old_version = config.current_bot_version
    config.current_bot_version = version
    config.current_bot_release_date = datetime.now(timezone.utc).strftime("%b %d %Y")
    await session.commit()
    return {
        "previous_version": old_version,
        "updated_to": version,
        "release_date": config.current_bot_release_date,
        "exe_ready": True,
        "image_ready": True,
    }
