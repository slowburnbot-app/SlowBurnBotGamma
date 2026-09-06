"""Exe-facing endpoints — called by the compiled SlowBurnBot client."""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_active_user
from app.crypto import decrypt
from app.database import commit_tolerating_race, get_async_session
from app.deps import (
    get_active_subscription,
    is_subscription_entitled,
    require_active_subscription,
)
from app.models.account import Account
from app.models.account_settings import AccountSettings
from app.models.activity_log import ActivityLog
from app.models.desktop_build import DesktopBuild
from app.models.follow_seed import FollowSeed
from app.models.follow_target import FollowTarget
from app.models.ignore_handle import IgnoreHandle
from app.models.client_heartbeat import ClientHeartbeat
from app.models.session_log import SessionLog
from app.models.subscription import Subscription
from app.models.system_config import SystemConfig
from app.models.user import User
from app.models.user_config import UserConfig
from app.schemas.account import AccountRead
from app.schemas.account_settings import AccountSettingsRead
from app.schemas.action_limit import (
    ActionLimitCreate,
    ActiveActionLimit,
    ReleasedLimit,
    StatusPageCheckCreate,
    StatusPageCheckResult,
)
from app.schemas.bot import (
    ActivityLogCreate,
    BotSettingsRead,
    BotUserConfigRead,
    BotUserConfigUpdate,
    BotNotifyRequest,
    ClientAccountState,
    ClientStateRead,
    CredentialsRead,
    EntitlementRead,
    FollowTargetCreate,
    FollowTargetRead,
    FollowTargetUpdate,
    HeartbeatCreate,
    IgnoreHandlesRead,
    RunCountRead,
    SessionLogCreate,
)
from app.schemas.desktop_build import DesktopActivateRequest
from app.schemas.follow_seed import BotFollowSeedCreate, BotFollowSeedUpdate, FollowSeedListRead, FollowSeedRead
from app.services.action_limits import active_limits, record_action_limit, record_status_page_check
from app.services.follow_seeds import list_seeds, normalize_handle, seed_stats_by_handle, seed_to_dict
from app.services.notifications import NotificationError, send_email, send_sms
from app.settings import settings

router = APIRouter(prefix="/bot", tags=["bot"])


async def _assert_account_owned(
    account_id: uuid.UUID, user: User, session: AsyncSession
) -> Account:
    result = await session.execute(
        select(Account).where(Account.id == account_id, Account.user_id == user.id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return account


@router.get("/entitlement", response_model=EntitlementRead)
async def check_entitlement(
    subscription: Subscription | None = Depends(get_active_subscription),
):
    """Startup entitlement check for the exe."""
    if subscription is None:
        return EntitlementRead(active=False, plan_tier="free")
    return EntitlementRead(
        active=is_subscription_entitled(subscription),
        plan_tier=subscription.plan_tier,
        current_period_end=subscription.current_period_end,
    )


@router.get("/config", response_model=BotUserConfigRead)
async def get_bot_config(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Fetch user-wide config (notification prefs) for the exe."""
    result = await session.execute(
        select(UserConfig).where(UserConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = UserConfig(user_id=user.id, notify_email=user.email)
        session.add(config)
    if config.vnc_pin is None:
        config.vnc_pin = secrets.token_hex(4).upper()
    if await commit_tolerating_race(session):
        await session.refresh(config)
    else:
        result = await session.execute(select(UserConfig).where(UserConfig.user_id == user.id))
        config = result.scalar_one()
    return config


@router.put("/config", response_model=BotUserConfigRead)
async def update_bot_config(
    body: BotUserConfigUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Persist notification prefs edited from the exe's /settings screen.

    Mirrors the website's PUT /config (app/routers/config.py::update_config) — same
    underlying UserConfig row, just a narrower field set.
    """
    result = await session.execute(
        select(UserConfig).where(UserConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = UserConfig(user_id=user.id, notify_email=user.email, login_notify_email=user.email)
        session.add(config)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(config, field, value)

    await session.commit()
    await session.refresh(config)
    return config


@router.get("/client-state", response_model=ClientStateRead)
async def get_client_state(
    group_number: int | None = Query(None),
    known_version: str | None = Query(None),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    subscription: Subscription | None = Depends(get_active_subscription),
):
    """Consolidated exe poll: entitlement + user config + accounts + settings in one request.

    Returns `changed: false` (with accounts/user_config omitted) when the client's
    `known_version` matches the current payload hash, so the client can skip work.
    `entitlement` is always returned fresh regardless of version so mid-run lapses
    are detected promptly.
    """
    # Entitlement — always fresh
    entitlement = EntitlementRead(
        active=is_subscription_entitled(subscription),
        plan_tier=subscription.plan_tier if subscription else "free",
        current_period_end=subscription.current_period_end if subscription else None,
    )

    # UserConfig — auto-create with vnc_pin if missing (mirrors get_bot_config)
    cfg_result = await session.execute(select(UserConfig).where(UserConfig.user_id == user.id))
    config = cfg_result.scalar_one_or_none()
    if config is None:
        config = UserConfig(user_id=user.id, notify_email=user.email)
        session.add(config)
    if config.vnc_pin is None:
        config.vnc_pin = secrets.token_hex(4).upper()
    if await commit_tolerating_race(session):
        await session.refresh(config)
    else:
        cfg_result = await session.execute(select(UserConfig).where(UserConfig.user_id == user.id))
        config = cfg_result.scalar_one()
    user_config = BotUserConfigRead.model_validate(config, from_attributes=True)

    # Accounts filtered by group
    acct_query = select(Account).where(Account.user_id == user.id)
    if group_number is not None:
        acct_query = acct_query.where(Account.group_number == group_number)
    acct_result = await session.execute(acct_query)
    accounts = list(acct_result.scalars().all())

    # Settings — one bulk query keyed by account_id
    settings_by_id: dict[uuid.UUID, AccountSettings] = {}
    if accounts:
        ids = [a.id for a in accounts]
        s_result = await session.execute(
            select(AccountSettings).where(AccountSettings.account_id.in_(ids))
        )
        settings_by_id = {s.account_id: s for s in s_result.scalars().all()}

    # Auto-create default settings for any account that doesn't have a row yet
    new_settings: list[AccountSettings] = []
    for account in accounts:
        if account.id not in settings_by_id:
            s = AccountSettings(account_id=account.id, user_id=user.id)
            session.add(s)
            new_settings.append(s)
            settings_by_id[account.id] = s
    if new_settings:
        if await commit_tolerating_race(session):
            for s in new_settings:
                await session.refresh(s)
        else:
            # A concurrent poll won the insert for at least one of these
            # accounts. Re-select what exists now and retry once for
            # whichever accounts are still missing a row.
            s_result = await session.execute(
                select(AccountSettings).where(AccountSettings.account_id.in_(ids))
            )
            settings_by_id = {s.account_id: s for s in s_result.scalars().all()}
            retry_new = []
            for account in accounts:
                if account.id not in settings_by_id:
                    s = AccountSettings(account_id=account.id, user_id=user.id)
                    session.add(s)
                    retry_new.append(s)
                    settings_by_id[account.id] = s
            if retry_new and await commit_tolerating_race(session):
                for s in retry_new:
                    await session.refresh(s)

    # Active action-limit cooldowns, one query for all accounts. They enter the
    # payload hash below, so a new or shortened cooldown busts the client cache.
    limits_by_id = await active_limits(session, [a.id for a in accounts])

    # Build serialisable account list
    account_states: list[ClientAccountState] = []
    for account in accounts:
        acct_read = AccountRead.model_validate(account, from_attributes=True).model_copy(
            update={
                "has_password": account.ig_password_enc is not None,
                "action_limits": limits_by_id.get(account.id, []),
            }
        )
        s = settings_by_id.get(account.id)
        s_read = AccountSettingsRead.model_validate(s, from_attributes=True) if s else None
        account_states.append(ClientAccountState(**acct_read.model_dump(), settings=s_read))

    # Current bot version from SystemConfig (singleton)
    sc_result = await session.execute(select(SystemConfig))
    sc = sc_result.scalar_one_or_none()
    current_bot_version = sc.current_bot_version if sc else None

    # Compute version hash from the serialised payload
    bundle: dict = {
        "user_config": user_config.model_dump(mode="json"),
        "accounts": [
            {
                **a.model_dump(mode="json", exclude={"settings"}),
                "settings": a.settings.model_dump(mode="json") if a.settings else None,
            }
            for a in account_states
        ],
    }
    version = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, default=str).encode()
    ).hexdigest()

    if known_version and known_version == version:
        return ClientStateRead(
            version=version,
            changed=False,
            entitlement=entitlement,
            current_bot_version=current_bot_version,
        )

    return ClientStateRead(
        version=version,
        changed=True,
        entitlement=entitlement,
        current_bot_version=current_bot_version,
        user_config=user_config,
        accounts=account_states,
    )


@router.post("/notify")
async def post_bot_notify(
    body: BotNotifyRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """Dispatch a notification on behalf of the bot.

    The bot describes the event (channel + recipient + content); the backend
    holds the SMTP/TextBelt credentials and does the actual send.
    """
    try:
        if body.channel == "email":
            await send_email(
                to=body.to,
                subject=body.subject or "SlowBurnBot",
                body=body.body,
                session=session,
            )
        else:
            await send_sms(to=body.to, body=body.body, session=session)
    except NotificationError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        )
    return {"ok": True}


@router.get("/settings/{account_id}", response_model=BotSettingsRead)
async def get_bot_settings(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """Fetch account settings for the exe — requires active subscription.

    Also carries the account's active action-limit cooldowns and the time of
    its last Account Status read, so the run-start fetch tells the bot which
    actions to skip and whether the daily status check is due."""
    account = await _assert_account_owned(account_id, user, session)
    result = await session.execute(
        select(AccountSettings).where(AccountSettings.account_id == account_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = AccountSettings(account_id=account_id, user_id=user.id)
        session.add(settings)
        if await commit_tolerating_race(session):
            await session.refresh(settings)
        else:
            result = await session.execute(
                select(AccountSettings).where(AccountSettings.account_id == account_id)
            )
            settings = result.scalar_one()
    limits = await active_limits(session, [account.id])
    return BotSettingsRead(
        **AccountSettingsRead.model_validate(settings, from_attributes=True).model_dump(),
        action_limits=limits.get(account.id, []),
        status_page_checked_at=account.status_page_checked_at,
    )


@router.post("/action-limit", response_model=ActiveActionLimit, status_code=status.HTTP_201_CREATED)
async def post_action_limit(
    body: ActionLimitCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """The exe detected Instagram limiting an action. Hard-tier reports start
    an escalating cooldown; the reply carries `until` and the strike count."""
    account = await _assert_account_owned(body.account_id, user, session)
    row = await record_action_limit(
        session, account, body.action, body.tier, body.reason, body.details
    )
    return ActiveActionLimit.model_validate(row)


@router.post("/status-page-check", response_model=StatusPageCheckResult)
async def post_status_page_check(
    body: StatusPageCheckCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """The exe read Instagram's Account Status pages. A clean read shortens
    any repeat (48h/72h) cooldown that has already run 24h back to 24h."""
    account = await _assert_account_owned(body.account_id, user, session)
    released = await record_status_page_check(session, account, body.clean)
    if body.text:
        session.add(ActivityLog(
            account_id=account.id, user_id=user.id, kind="status_page",
            action="account-status", status=account.status_page_result,
            details=body.text,
        ))
        await session.commit()
    return StatusPageCheckResult(
        released=[ReleasedLimit(action=r.action, until=r.until) for r in released]
    )


@router.post("/session-log", status_code=status.HTTP_201_CREATED)
async def post_session_log(
    body: SessionLogCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Batch session summary from the exe at end of run."""
    await _assert_account_owned(body.account_id, user, session)
    log = SessionLog(**body.model_dump(), user_id=user.id)
    session.add(log)
    await session.commit()
    return {"id": str(log.id)}


@router.post("/activity-log", status_code=status.HTTP_201_CREATED)
async def post_activity_log(
    body: ActivityLogCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Fine-grained activity or error event from the exe."""
    await _assert_account_owned(body.account_id, user, session)
    log = ActivityLog(**body.model_dump(), user_id=user.id)
    session.add(log)
    await session.commit()
    return {"id": str(log.id)}


@router.get("/credentials/{account_id}", response_model=CredentialsRead)
async def get_bot_credentials(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """Return decrypted IG password for the exe — requires active subscription."""
    account = await _assert_account_owned(account_id, user, session)
    ig_password = decrypt(account.ig_password_enc) if account.ig_password_enc else None
    return CredentialsRead(ig_password=ig_password)


@router.get("/ignore-handles", response_model=IgnoreHandlesRead)
async def get_ignore_handles(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Return all ignore handles for the user."""
    result = await session.execute(
        select(IgnoreHandle.handle).where(IgnoreHandle.user_id == user.id)
    )
    handles = [row[0] for row in result.all()]
    return IgnoreHandlesRead(handles=handles)


@router.get("/follow-targets/{account_id}", response_model=list[FollowTargetRead])
async def get_bot_follow_targets(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    status_filter: str | None = Query(None, alias="status"),
    older_than_days: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    """Return follow targets for an account with optional filtering."""
    await _assert_account_owned(account_id, user, session)
    query = select(FollowTarget).where(FollowTarget.account_id == account_id)
    if status_filter:
        query = query.where(FollowTarget.status == status_filter)
    if older_than_days is not None:
        cutoff = date.today() - timedelta(days=older_than_days)
        query = query.where(FollowTarget.follow_date <= cutoff)
    query = query.order_by(FollowTarget.follow_date.asc().nullslast())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    return [FollowTargetRead.model_validate(t, from_attributes=True) for t in result.scalars().all()]


@router.post("/follow-targets", status_code=status.HTTP_201_CREATED)
async def create_bot_follow_target(
    body: FollowTargetCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new follow target record from the exe."""
    await _assert_account_owned(body.account_id, user, session)
    target = FollowTarget(**body.model_dump(), user_id=user.id)
    session.add(target)
    await session.commit()
    return {"id": str(target.id)}


@router.patch("/follow-targets/{target_id}")
async def update_bot_follow_target(
    target_id: uuid.UUID,
    body: FollowTargetUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Update a follow target (status, unfollow_date, follow_back) from the exe."""
    result = await session.execute(
        select(FollowTarget).where(FollowTarget.id == target_id, FollowTarget.user_id == user.id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow target not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    await session.commit()
    return {"id": str(target.id)}


@router.get("/run-count/{account_id}", response_model=RunCountRead)
async def get_run_count(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    run_date: date | None = Query(None),
):
    """Count session logs for an account on a given date (defaults to today)."""
    await _assert_account_owned(account_id, user, session)
    target_date = run_date or date.today()
    count = await session.scalar(
        select(func.count()).where(
            SessionLog.account_id == account_id,
            SessionLog.run_date == target_date,
        )
    )
    return RunCountRead(count=count or 0)


@router.post("/heartbeat")
async def post_heartbeat(
    body: HeartbeatCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Upsert client heartbeat — called every ~60s by the bot client."""
    result = await session.execute(
        select(ClientHeartbeat).where(
            ClientHeartbeat.user_id == user.id,
            ClientHeartbeat.client_id == body.client_id,
        )
    )
    hb = result.scalar_one_or_none()
    if hb is None:
        hb = ClientHeartbeat(user_id=user.id, client_id=body.client_id)
        session.add(hb)
    hb.system_type = body.system_type
    hb.ip_address = body.ip_address
    hb.status = body.status
    if body.current_account is not None:
        hb.last_session_account = body.current_account
    hb.current_account = body.current_account
    hb.last_heartbeat = datetime.now(timezone.utc)
    # Keep the desktop build's recorded version in sync with the version the
    # client is actually running, so the clients page reflects live state rather
    # than the version captured once at activation. Unique (user_id, client_id)
    # means this touches exactly one row; is_distinct_from avoids a write every
    # heartbeat when the version hasn't changed.
    if body.bot_version:
        await session.execute(
            update(DesktopBuild)
            .where(
                DesktopBuild.user_id == user.id,
                DesktopBuild.client_id == body.client_id,
                DesktopBuild.bot_version.is_distinct_from(body.bot_version),
            )
            .values(bot_version=body.bot_version)
        )
    # A live authenticated heartbeat is stronger proof of activation than the
    # first-launch token handshake — a slot recreated (or configured without
    # consuming its token) while its client keeps running via JWT would
    # otherwise show "pending" forever. The activate endpoint no longer keys
    # its lookup on status, so this flip can't break a pending token handshake.
    await session.execute(
        update(DesktopBuild)
        .where(
            DesktopBuild.user_id == user.id,
            DesktopBuild.client_id == body.client_id,
            DesktopBuild.status == "pending_activation",
        )
        .values(status="activated", activated_at=datetime.now(timezone.utc))
    )
    # A concurrent heartbeat for the same (user_id, client_id) racing this
    # insert is harmless to ignore — another call landed equivalent data
    # within the same ~60s tick, so there's nothing left to apply here.
    await commit_tolerating_race(session)
    return {"ok": True}


@router.post("/desktop/activate")
async def activate_desktop_build(
    body: DesktopActivateRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Single-use activation handshake called by the generic binary on first launch.
    No JWT required — validated by the activation token instead.
    Returns build_options so the client can write its local burnBot_config.ini.
    """
    now = datetime.now(timezone.utc)

    # No status filter — unique (user_id, client_id) yields one row, and the
    # consumed/expiry/hash checks below enforce single-use. A heartbeat may
    # flip a pending slot to "activated" before its token handshake runs
    # (an old container heartbeating the same slot); the token must still work.
    build = await session.scalar(
        select(DesktopBuild).where(
            DesktopBuild.user_id == body.user_id,
            DesktopBuild.client_id == body.client_id,
        )
    )
    if build is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build not found.")

    # Single-use enforcement: reject if already consumed
    if build.consumed_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Activation token already used.")

    if build.activation_token_expires_at is None or build.activation_token_expires_at < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Activation token expired.")

    if build.activation_token_hash is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Activation token revoked.")

    token_hash = hashlib.sha256(body.activation_token.encode()).hexdigest()
    if not hmac.compare_digest(build.activation_token_hash, token_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid activation token.")

    # Consume the token — null out hash so it can never match again
    build.activated_at = now
    build.consumed_at = now
    build.activation_token_hash = None
    build.activation_token_expires_at = None
    build.status = "activated"
    if body.bot_version:
        build.bot_version = body.bot_version
    await session.commit()

    return {
        "activated": True,
        "client_id": build.client_id,
        "api_url": settings.public_api_url,
        "build_options": build.build_options,
    }


# ── follow seeds ──────────────────────────────────────────────────────────────


@router.get("/seeds/{account_id}", response_model=FollowSeedListRead)
async def get_bot_seeds(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """All seeds (retired included) with follow-back numbers. The bot picks from
    the active ones and uses the full list to avoid re-discovering retired handles."""
    await _assert_account_owned(account_id, user, session)
    return await list_seeds(session, account_id)


@router.post("/seeds", response_model=FollowSeedRead, status_code=status.HTTP_201_CREATED)
async def create_bot_seed(
    body: BotFollowSeedCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """Bot-discovered seed (origin 'similar:<parent>'). Existing handles are returned as-is."""
    await _assert_account_owned(body.account_id, user, session)
    handle = normalize_handle(body.handle)
    if not handle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Handle is required.")
    result = await session.execute(
        select(FollowSeed).where(FollowSeed.account_id == body.account_id, FollowSeed.handle == handle)
    )
    seed = result.scalar_one_or_none()
    if seed is None:
        seed = FollowSeed(user_id=user.id, account_id=body.account_id, handle=handle, origin=body.origin, active=True)
        session.add(seed)
        await session.commit()
        await session.refresh(seed)
    stats, _ = await seed_stats_by_handle(session, body.account_id)
    return seed_to_dict(seed, stats)


@router.patch("/seeds/{seed_id}", response_model=FollowSeedRead)
async def update_bot_seed(
    seed_id: uuid.UUID,
    body: BotFollowSeedUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    """Record a use (last_used_at / last_saturation) or retire a seed."""
    result = await session.execute(
        select(FollowSeed).where(FollowSeed.id == seed_id, FollowSeed.user_id == user.id)
    )
    seed = result.scalar_one_or_none()
    if seed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seed not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        setattr(seed, field, value)
    if body.active is False:
        seed.retired_at = datetime.now(timezone.utc)
    elif body.active is True:
        seed.retired_at = None
        seed.retire_reason = None
    await session.commit()
    await session.refresh(seed)
    stats, _ = await seed_stats_by_handle(session, seed.account_id)
    return seed_to_dict(seed, stats)
