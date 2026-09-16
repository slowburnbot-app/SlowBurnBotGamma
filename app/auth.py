import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException
from fastapi_users import FastAPIUsers, BaseUserManager, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
)
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.access_token import AccessToken
from app.models.user import User
from app.settings import settings

# Was 30 days on a stateless signed JWT with no way to revoke it — a stolen
# token stayed valid for the full 30 days no matter what the legitimate user
# did (password change, logout, nothing invalidated it; see auth_refresh.py's
# old TODO). Now backed by DatabaseStrategy (below): every request looks the
# token up in access_tokens, so logout and a password change can actually
# delete it. Lifetime is still meaningfully shortened as defense-in-depth for
# the window before either of those happens.
ACCESS_TOKEN_LIFETIME = 1209600  # 14 days


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


async def get_access_token_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)


async def _revoke_all_access_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete every outstanding session for a user — used on password change
    so a token issued before the change (e.g. one an attacker stole) stops
    working immediately instead of remaining valid for the rest of its
    lifetime."""
    await session.execute(delete(AccessToken).where(AccessToken.user_id == user_id))
    await session.commit()


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def on_after_reset_password(self, user: User, request=None) -> None:
        """/auth/reset-password isn't currently mounted (main.py doesn't
        include get_reset_password_router), but implement the revocation
        hook now so it's correct the moment that flow is wired up."""
        await _revoke_all_access_tokens(self.user_db.session, user.id)

    async def on_after_update(
        self, user: User, update_dict: dict[str, Any], request=None
    ) -> None:
        """Fires after PATCH /users/me (mounted today). Revoke existing
        sessions only when the password itself changed — not on every
        profile edit."""
        if "password" in update_dict:
            await _revoke_all_access_tokens(self.user_db.session, user.id)

    async def create(self, user_create, safe=False, request=None):
        from app.database import async_session_maker
        from app.models.invite_code import InviteCode

        code_str = getattr(user_create, "invite_code", None)
        if not code_str:
            raise HTTPException(status_code=400, detail="Registration code is required.")

        async with async_session_maker() as session:
            result = await session.execute(
                select(InviteCode).where(InviteCode.code == code_str)
            )
            invite = result.scalar_one_or_none()
            if invite is None:
                raise HTTPException(status_code=400, detail="Invalid registration code.")
            if invite.used_by_user_id is not None:
                raise HTTPException(status_code=400, detail="This registration code has already been used.")
            if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="This registration code has expired.")
            if invite.email and invite.email.lower() != user_create.email.lower():
                raise HTTPException(status_code=400, detail="This registration code is tied to a different email address.")

        user = await super().create(user_create, safe, request)
        return user

    async def on_after_register(self, user: User, request=None):
        from app.models.subscription import Subscription
        from app.models.invite_code import InviteCode
        from app.database import async_session_maker

        # Pull the invite code off the request body, if any.
        code_str: str | None = None
        if request:
            try:
                body = await request.json()
                raw = body.get("invite_code")
                if isinstance(raw, str) and raw.strip():
                    code_str = raw.strip()
            except Exception:
                pass

        async with async_session_maker() as session:
            invite: InviteCode | None = None

            if code_str:
                # Atomic claim: only succeeds if the invite is still unclaimed.
                # Wins the race against any concurrent registration with the
                # same code; the loser's super().create() user is deleted
                # below and the request fails.
                claim_stmt = (
                    update(InviteCode)
                    .where(
                        InviteCode.code == code_str,
                        InviteCode.used_by_user_id.is_(None),
                    )
                    .values(
                        used_by_user_id=user.id,
                        used_at=datetime.now(timezone.utc),
                    )
                    .returning(InviteCode)
                )
                claimed = await session.execute(claim_stmt)
                invite = claimed.scalar_one_or_none()

                if invite is None:
                    # Either the code never existed or another registration
                    # claimed it first.  Roll back the user that fastapi-users
                    # already committed before raising.
                    await session.execute(
                        delete(User).where(User.id == user.id)
                    )
                    await session.commit()
                    raise HTTPException(
                        status_code=400,
                        detail="This registration code has already been used.",
                    )

            sub_status = "inactive"
            plan_tier = "free"
            period_end = None

            if invite and invite.free_trial_days:
                sub_status = "trialing"
                plan_tier = invite.plan_tier
                period_end = datetime.now(timezone.utc) + timedelta(days=invite.free_trial_days)

                user_result = await session.execute(
                    select(User).where(User.id == user.id)
                )
                db_user = user_result.scalar_one_or_none()
                if db_user:
                    db_user.plan_tier = plan_tier

            subscription = Subscription(
                user_id=user.id,
                status=sub_status,
                plan_tier=plan_tier,
                current_period_end=period_end,
            )
            session.add(subscription)
            await session.commit()

            from app.services.admin_notify import notify_admin

            lines = [
                "A new account was created.",
                "",
                f"email:        {user.email}",
                f"invite code:  {invite.code if invite else '----'}",
                f"plan tier:    {plan_tier}",
                f"status:       {sub_status}",
                f"trial days:   {invite.free_trial_days if invite and invite.free_trial_days else '----'}",
                f"trial ends:   {period_end.strftime('%Y-%m-%d %H:%M UTC') if period_end else '----'}",
                "",
                "Review at /admin/users.",
            ]
            await notify_admin("new account created", lines, session)


async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="/auth/jwt/login")


def get_database_strategy(
    access_token_db=Depends(get_access_token_db),
) -> DatabaseStrategy:
    return DatabaseStrategy(access_token_db, lifetime_seconds=ACCESS_TOKEN_LIFETIME)


# name="jwt" and the "/auth/jwt/*" route prefix (set where this backend is
# mounted in main.py) are historical — tokens are opaque DB-backed strings
# now, not JWTs. Left as-is: it's just an internal label, not part of the
# token format, and renaming would be a gratuitous diff.
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_database_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_active_user_optional = fastapi_users.current_user(active=True, optional=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
