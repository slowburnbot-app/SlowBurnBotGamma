"""Customer-facing subscription info."""
import stripe
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_active_user
from app.database import get_async_session
from app.deps import get_active_subscription
from app.models.account import Account
from app.models.desktop_build import DesktopBuild
from app.models.subscription import Subscription
from app.models.user import User
from app.plan_tiers import PLAN_TIERS, get_max_accounts, get_max_clients, is_valid_tier
from app.services.stripe_sync import price_id_for_tier
from app.settings import settings

router = APIRouter(prefix="/subscription", tags=["subscription"])


class TierInfo(BaseModel):
    name: str
    price: int
    max_accounts: int
    max_clients: int


class SubscriptionInfoRead(BaseModel):
    plan_tier: str
    status: str
    max_accounts: int
    current_accounts: int
    max_clients: int
    current_clients: int
    current_period_end: str | None = None
    tiers: list[TierInfo]
    # Whether this account has a real Stripe Customer behind it — status
    # alone can't tell you that (an admin-activated or invite-trial account
    # is "active"/"trialing" with no Stripe link at all). Frontend uses this
    # to decide Checkout (no Stripe customer yet) vs. Billing Portal (one
    # exists) — mirrors exactly what /subscription/portal itself checks.
    has_stripe_customer: bool


@router.get("/me", response_model=SubscriptionInfoRead)
async def get_subscription_info(
    user: User = Depends(current_active_user),
    subscription: Subscription | None = Depends(get_active_subscription),
    session: AsyncSession = Depends(get_async_session),
):
    plan_tier = subscription.plan_tier if subscription else "free"
    sub_status = subscription.status if subscription else "inactive"
    period_end = (
        subscription.current_period_end.isoformat()
        if subscription and subscription.current_period_end
        else None
    )

    current_accounts = await session.scalar(
        select(func.count()).where(Account.user_id == user.id)
    )

    current_clients = await session.scalar(
        select(func.count()).where(
            DesktopBuild.user_id == user.id,
            DesktopBuild.status.notin_(DesktopBuild.NON_OCCUPYING_STATUSES),
        )
    )

    tiers = [
        TierInfo(
            name=name,
            price=info["price"],
            max_accounts=info["max_accounts"],
            max_clients=info["max_clients"],
        )
        for name, info in PLAN_TIERS.items()
    ]

    return SubscriptionInfoRead(
        plan_tier=plan_tier,
        status=sub_status,
        max_accounts=get_max_accounts(plan_tier),
        current_accounts=current_accounts,
        max_clients=get_max_clients(plan_tier),
        current_clients=current_clients,
        current_period_end=period_end,
        tiers=tiers,
        has_stripe_customer=bool(subscription and subscription.stripe_customer_id),
    )


class CheckoutRequest(BaseModel):
    plan_tier: str


class RedirectUrl(BaseModel):
    url: str


@router.post("/checkout", response_model=RedirectUrl)
async def create_checkout_session(
    body: CheckoutRequest,
    user: User = Depends(current_active_user),
    subscription: Subscription | None = Depends(get_active_subscription),
    session: AsyncSession = Depends(get_async_session),
):
    """Start a new paid subscription via Stripe-hosted Checkout.

    For plan changes on an existing paid subscription, use the Customer
    Portal (`/subscription/portal`) instead — Checkout always creates a new
    Stripe subscription, so running it against an account that already has
    one would leave the customer with two.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured.")

    if not is_valid_tier(body.plan_tier):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.plan_tier}")

    price_id = price_id_for_tier(body.plan_tier)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"No Stripe price configured for tier: {body.plan_tier}")

    if subscription is None:
        subscription = Subscription(user_id=user.id)
        session.add(subscription)

    if subscription.stripe_subscription_id and subscription.status in ("active", "trialing", "past_due"):
        raise HTTPException(
            status_code=400,
            detail="Already subscribed — use the billing portal to change plans.",
        )

    stripe.api_key = settings.stripe_secret_key

    # Lazily create (and persist) the Stripe Customer before redirecting, so
    # the webhook that lands after checkout can find this row by
    # stripe_customer_id on the very first subscription — apply_stripe_sub
    # -scription's lookup has nothing else to match on a brand-new customer.
    if not subscription.stripe_customer_id:
        customer = await run_in_threadpool(
            stripe.Customer.create, email=user.email, metadata={"user_id": str(user.id)}
        )
        subscription.stripe_customer_id = customer.id
        await session.commit()

    checkout_session = await run_in_threadpool(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=subscription.stripe_customer_id,
        client_reference_id=str(user.id),
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.frontend_base_url}/dashboard/account?checkout=success",
        cancel_url=f"{settings.frontend_base_url}/dashboard/account?checkout=cancel",
        # Managed Payments is on by default for this account and requires a
        # product tax code on every line item otherwise. We're handling tax
        # ourselves (Stripe Tax threshold monitoring, not active collection
        # yet) rather than opting into Managed Payments, so turn it off here.
        managed_payments={"enabled": False},
    )
    return RedirectUrl(url=checkout_session.url)


@router.post("/portal", response_model=RedirectUrl)
async def create_portal_session(
    user: User = Depends(current_active_user),
    subscription: Subscription | None = Depends(get_active_subscription),
):
    """Open the Stripe Customer Portal for self-serve upgrade/downgrade,
    cancellation, and payment-method updates on an existing subscription."""
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured.")

    if subscription is None or not subscription.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer on record yet.")

    stripe.api_key = settings.stripe_secret_key
    portal_session = await run_in_threadpool(
        stripe.billing_portal.Session.create,
        customer=subscription.stripe_customer_id,
        return_url=f"{settings.frontend_base_url}/dashboard/account",
    )
    return RedirectUrl(url=portal_session.url)
