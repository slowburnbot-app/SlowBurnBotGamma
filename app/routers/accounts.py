"""Account CRUD — dashboard use."""
import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import and_, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_active_user
from app.crypto import encrypt
from app.database import commit_tolerating_race, get_async_session
from app.deps import require_active_subscription
from app.plan_tiers import get_max_accounts
from app.models.account import Account
from app.models.account_settings import AccountSettings
from app.models.activity_log import ActivityLog
from app.models.client_heartbeat import ClientHeartbeat
from app.models.desktop_build import DesktopBuild
from app.models.follow_seed import FollowSeed
from app.models.follow_target import FollowTarget
from app.models.session_log import SessionLog
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate
from app.schemas.account_settings import AccountSettingsRead, AccountSettingsUpdate
from app.schemas.follow_seed import FollowSeedCreate, FollowSeedListRead, FollowSeedRead, FollowSeedUpdate
from app.services.follow_seeds import list_seeds, normalize_handle, seed_stats_by_handle, seed_to_dict

router = APIRouter(prefix="/accounts", tags=["accounts"])


async def _get_owned_account(
    account_id: uuid.UUID,
    user: User,
    session: AsyncSession,
) -> Account:
    result = await session.execute(
        select(Account).where(Account.id == account_id, Account.user_id == user.id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return account


def _account_read(account: Account) -> AccountRead:
    return AccountRead.model_validate(account, from_attributes=True).model_copy(
        update={"has_password": account.ig_password_enc is not None}
    )


def _clean_session_action_type(raw: str | None) -> str | None:
    """Strip placeholders and mis-mapped numeric cells (bad runlog imports) from action type fields."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in ("—", "--", "-"):
        return None
    try:
        float(s.replace(",", ""))
        return None
    except ValueError:
        return s


@router.get("", response_model=list[AccountRead])
async def list_accounts(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    group_number: int | None = Query(None),
):
    query = select(Account).where(Account.user_id == user.id)
    if group_number is not None:
        query = query.where(Account.group_number == group_number)
    result = await session.execute(query)
    return [_account_read(a) for a in result.scalars().all()]


@router.get("/client-status", response_model=list)
async def get_client_status(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Return heartbeat rows for clients that have an active (non-revoked, non-failed) build."""
    cutoff = func.now() - timedelta(minutes=2)
    active_client_ids = (
        select(DesktopBuild.client_id)
        .where(
            DesktopBuild.user_id == user.id,
            DesktopBuild.status.notin_(DesktopBuild.NON_OCCUPYING_STATUSES),
        )
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            ClientHeartbeat,
            (ClientHeartbeat.last_heartbeat >= cutoff).label("connected"),
            DesktopBuild.build_options["client_name"].astext.label("client_name"),
        )
        .join(
            DesktopBuild,
            (DesktopBuild.user_id == ClientHeartbeat.user_id)
            & (DesktopBuild.client_id == ClientHeartbeat.client_id)
            & DesktopBuild.status.notin_(DesktopBuild.NON_OCCUPYING_STATUSES),
            isouter=True,
        )
        .where(
            ClientHeartbeat.user_id == user.id,
            ClientHeartbeat.client_id.in_(active_client_ids),
        )
        .order_by(ClientHeartbeat.client_id)
    )
    rows = result.all()
    return [
        {
            "client_id": r.ClientHeartbeat.client_id,
            "client_name": r.client_name or "",
            "system_type": r.ClientHeartbeat.system_type,
            "status": r.ClientHeartbeat.status,
            "current_account": r.ClientHeartbeat.current_account,
            "last_session_account": r.ClientHeartbeat.last_session_account,
            "last_heartbeat": r.ClientHeartbeat.last_heartbeat.isoformat() if r.ClientHeartbeat.last_heartbeat else None,
            "connected": bool(r.connected),
        }
        for r in rows
    ]


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    _: Subscription = Depends(require_active_subscription),
):
    # Enforce account limit for the user's plan tier
    max_accounts = get_max_accounts(user.plan_tier)
    count_result = await session.execute(
        select(func.count()).where(Account.user_id == user.id)
    )
    current_count = count_result.scalar_one()
    if current_count >= max_accounts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account limit reached for your plan ({max_accounts} accounts).",
        )

    data = body.model_dump(exclude={"ig_password"})
    if body.ig_password:
        data["ig_password_enc"] = encrypt(body.ig_password)
    account = Account(**data, user_id=user.id)
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return _account_read(account)


def _period_start(period: str, today: date | None = None) -> date:
    """Anchor on the caller's local date when provided — the server's UTC
    date.today() disagrees with client-stamped run_date for hours each day."""
    today = today or date.today()
    if period == "week":
        return today - timedelta(days=7)
    if period == "month":
        return today - timedelta(days=30)
    return today  # day


def _action_sum(type_col, count_col, action_type: str):
    return func.coalesce(func.sum(case((type_col == action_type, count_col), else_=0)), 0)


@router.get("/log-summary", response_model=dict)
async def log_summary(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    period: str = Query("day"),
    client_date: date | None = Query(None),
):
    """Per-account aggregated session counts for a time period."""
    SL = SessionLog

    likes = sum(
        _action_sum(getattr(SL, f"action_{i}_type"), getattr(SL, f"action_{i}_count"), "like")
        for i in range(1, 5)
    )
    follows = sum(
        _action_sum(getattr(SL, f"action_{i}_type"), getattr(SL, f"action_{i}_count"), "follow")
        for i in range(1, 5)
    )
    unfollows = sum(
        _action_sum(getattr(SL, f"action_{i}_type"), getattr(SL, f"action_{i}_count"), "unfollow")
        for i in range(1, 5)
    )

    where_clauses = [SL.user_id == user.id]
    if period != "all":
        where_clauses.append(SL.run_date >= _period_start(period, client_date))
    result = await session.execute(
        select(
            SL.account_id,
            func.count().label("sessions"),
            likes.label("likes"),
            follows.label("follows"),
            unfollows.label("unfollows"),
        )
        .where(*where_clauses)
        .group_by(SL.account_id)
    )
    return {
        str(row.account_id): {
            "sessions": row.sessions,
            "likes": row.likes,
            "follows": row.follows,
            "unfollows": row.unfollows,
        }
        for row in result.all()
    }


@router.get("/followback-summary", response_model=dict)
async def followback_summary(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    period: str = Query("day"),
    client_date: date | None = Query(None),
):
    """Per-account follow-back rates for targets followed within a time period.

    All columns anchor on the same cohort — unfollow_date_cond, i.e. targets
    whose follow-back outcome was resolved (checked at unfollow time) within
    the period. `followed` previously anchored on follow_date instead, which
    measured a different set of targets than `complete`/`followed_back` —
    "Success Rate" ended up describing a cohort that didn't match the
    "followed" count displayed beside it.
    """
    FT = FollowTarget

    if period != "all":
        period_start = _period_start(period, client_date)
        unfollow_date_cond = FT.unfollow_date >= period_start
    else:
        unfollow_date_cond = true()

    result = await session.execute(
        select(
            FT.account_id,
            func.count().filter(unfollow_date_cond).label("followed"),
            func.count().filter(and_(unfollow_date_cond, FT.follow_back.isnot(None))).label("complete"),
            func.count().filter(and_(unfollow_date_cond, FT.follow_back == True)).label("followed_back"),
            func.min(FT.unfollow_date).filter(unfollow_date_cond).label("first_unfollow"),
            func.max(FT.unfollow_date).filter(unfollow_date_cond).label("last_unfollow"),
        )
        .where(FT.user_id == user.id)
        .group_by(FT.account_id)
    )
    rows = result.all()
    fixed_days = {"day": 1, "week": 7, "month": 30}.get(period)
    return {
        str(row.account_id): {
            "followed": row.followed,
            "complete": row.complete,
            "followed_back": row.followed_back,
            "rate": round(row.followed_back / row.complete, 2) if row.complete else None,
            "days": fixed_days if fixed_days else (
                max((row.last_unfollow - row.first_unfollow).days + 1, 1)
                if row.first_unfollow and row.last_unfollow else 1
            ),
        }
        for row in rows
    }


@router.get("/recent-log", response_model=dict)
async def get_recent_log_across_accounts(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    limit: int = Query(15, ge=1, le=100),
):
    """Most recent session log rows across all accounts owned by the user."""
    result = await session.execute(
        select(SessionLog, Account.name, Account.id)
        .join(Account, Account.id == SessionLog.account_id)
        .where(Account.user_id == user.id)
        .order_by(SessionLog.created_at.desc())
        .limit(limit)
    )

    def fmt_dt(dt):
        return dt.isoformat() if dt else None

    items = []
    for lg, account_name, account_id in result.all():
        items.append(
            {
                "id": str(lg.id),
                "account_id": str(account_id),
                "account_name": account_name,
                "run_date": lg.run_date.isoformat() if lg.run_date else None,
                "run_sequence": lg.run_sequence,
                "start_time": fmt_dt(lg.start_time),
                "end_time": fmt_dt(lg.end_time),
                "action_1_type": _clean_session_action_type(lg.action_1_type),
                "action_1_count": lg.action_1_count,
                "action_2_type": _clean_session_action_type(lg.action_2_type),
                "action_2_count": lg.action_2_count,
                "action_3_type": _clean_session_action_type(lg.action_3_type),
                "action_3_count": lg.action_3_count,
                "action_4_type": _clean_session_action_type(lg.action_4_type),
                "action_4_count": lg.action_4_count,
                "error_message": lg.error_message,
                "warning_message": lg.warning_message,
            }
        )
    return {"items": items}


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    return _account_read(await _get_owned_account(account_id, user, session))


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    account = await _get_owned_account(account_id, user, session)
    if account.system_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is system-disabled due to plan limits.",
        )
    data = body.model_dump(exclude_unset=True, exclude={"ig_password"})
    if "ig_password" in body.model_fields_set:
        data["ig_password_enc"] = encrypt(body.ig_password) if body.ig_password else None
    for field, value in data.items():
        setattr(account, field, value)
    await session.commit()
    await session.refresh(account)
    return _account_read(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    account = await _get_owned_account(account_id, user, session)
    await session.delete(account)
    await session.commit()


@router.get("/{account_id}/settings", response_model=AccountSettingsRead)
async def get_account_settings(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_owned_account(account_id, user, session)
    result = await session.execute(
        select(AccountSettings).where(AccountSettings.account_id == account_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        # create_account never inserts a row (defaults live here, not there);
        # auto-create on first read (mirrors bot.py's get_bot_settings) so a
        # brand-new account's dashboard settings page has something to load
        # and save instead of 404ing until a bot client happens to poll first.
        settings = AccountSettings(account_id=account_id, user_id=user.id)
        session.add(settings)
        if await commit_tolerating_race(session):
            await session.refresh(settings)
        else:
            result = await session.execute(
                select(AccountSettings).where(AccountSettings.account_id == account_id)
            )
            settings = result.scalar_one()
    return settings


@router.put("/{account_id}/settings", response_model=AccountSettingsRead)
async def upsert_account_settings(
    account_id: uuid.UUID,
    body: AccountSettingsUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    account = await _get_owned_account(account_id, user, session)
    if account.system_disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is system-disabled due to plan limits.",
        )
    result = await session.execute(
        select(AccountSettings).where(AccountSettings.account_id == account_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = AccountSettings(account_id=account_id, user_id=user.id)
        session.add(settings)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)

    await session.commit()
    await session.refresh(settings)
    return settings


SORT_COLUMNS = {
    "handle": FollowTarget.target_handle,
    "source": FollowTarget.source,
    "status": FollowTarget.status,
    "followed": FollowTarget.follow_date,
    "unfollowed": FollowTarget.unfollow_date,
    "fb": FollowTarget.follow_back,
}

@router.get("/{account_id}/database", response_model=dict)
async def get_account_database(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    sort: str = Query("followed"),
    sort_dir: str = Query("desc"),
):
    await _get_owned_account(account_id, user, session)
    offset = (page - 1) * page_size

    total = await session.scalar(
        select(func.count()).where(FollowTarget.account_id == account_id)
    )

    col = SORT_COLUMNS.get(sort, FollowTarget.follow_date)
    order = col.desc().nullslast() if sort_dir == "desc" else col.asc().nullsfirst()

    result = await session.execute(
        select(FollowTarget)
        .where(FollowTarget.account_id == account_id)
        .order_by(order, FollowTarget.target_handle)
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


@router.get("/{account_id}/database/export")
async def export_account_database(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    account = await _get_owned_account(account_id, user, session)

    result = await session.execute(
        select(FollowTarget)
        .where(FollowTarget.account_id == account_id)
        .order_by(FollowTarget.follow_date.desc().nullslast(), FollowTarget.target_handle)
    )
    targets = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["handle", "source", "status", "follow_date", "unfollow_date", "follow_back"])
    for t in targets:
        writer.writerow([
            t.target_handle,
            t.source or "",
            t.status,
            t.follow_date.isoformat() if t.follow_date else "",
            t.unfollow_date.isoformat() if t.unfollow_date else "",
            "yes" if t.follow_back is True else "no" if t.follow_back is False else "",
        ])

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in account.name) or "account"
    filename = f"{safe_name}_database.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{account_id}/stats", response_model=dict)
async def get_account_stats(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_owned_account(account_id, user, session)
    base = select(FollowTarget).where(FollowTarget.account_id == account_id)

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    )
    following = await session.scalar(
        select(func.count()).where(
            FollowTarget.account_id == account_id,
            FollowTarget.status == "following",
        )
    )

    settings_row = await session.scalar(
        select(AccountSettings).where(AccountSettings.account_id == account_id)
    )
    unfollow_days = (settings_row.unfollow_days if settings_row else 30) or 30
    unfollow_cutoff = datetime.now(timezone.utc) - timedelta(days=unfollow_days)
    unfollow_ready = await session.scalar(
        select(func.count()).where(
            FollowTarget.account_id == account_id,
            FollowTarget.status == "following",
            FollowTarget.follow_date.isnot(None),
            FollowTarget.follow_date <= unfollow_cutoff,
        )
    )
    ignored = await session.scalar(
        select(func.count()).where(
            FollowTarget.account_id == account_id,
            FollowTarget.status == "skipped",
        )
    )
    complete = await session.scalar(
        select(func.count()).where(
            FollowTarget.account_id == account_id,
            FollowTarget.follow_back.isnot(None),
        )
    )
    success = await session.scalar(
        select(func.count()).where(
            FollowTarget.account_id == account_id,
            FollowTarget.follow_back == True,
        )
    )

    # Last 25 fb rate
    last25_result = await session.execute(
        select(FollowTarget.follow_back)
        .where(
            FollowTarget.account_id == account_id,
            FollowTarget.follow_back.isnot(None),
        )
        .order_by(FollowTarget.follow_date.desc().nullslast())
        .limit(25)
    )
    last25_rows = [r[0] for r in last25_result.all()]
    last25_rate = sum(1 for x in last25_rows if x) / len(last25_rows) if last25_rows else None

    # All time fb rate
    all_time_rate = (success / complete) if complete else None

    return {
        "following": following or 0,
        "unfollow_ready": unfollow_ready or 0,
        "complete": complete or 0,
        "ignored": ignored or 0,
        "total": total or 0,
        "success": success or 0,
        "last_25": round(last25_rate, 2) if last25_rate is not None else None,
        "all_time": round(all_time_rate, 2) if all_time_rate is not None else None,
    }


@router.get("/{account_id}/source-stats", response_model=dict)
async def get_account_source_stats(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    period: str = Query("week"),
    client_date: date | None = Query(None),
):
    await _get_owned_account(account_id, user, session)

    # All columns anchor on the same cohort — unfollow_date_cond — so "total"
    # matches the population that "complete"/"followed_back"/"not_followed_back"
    # are drawn from (previously "total" anchored on follow_date instead,
    # measuring a different set of targets than the breakdown beside it).
    if period != "all":
        period_start = _period_start(period, client_date)
        unfollow_date_cond = FollowTarget.unfollow_date >= period_start
    else:
        unfollow_date_cond = true()

    result = await session.execute(
        select(
            FollowTarget.source,
            func.count().filter(unfollow_date_cond).label("total"),
            func.count().filter(and_(unfollow_date_cond, FollowTarget.follow_back.isnot(None))).label("complete"),
            func.count().filter(and_(unfollow_date_cond, FollowTarget.follow_back == True)).label("followed_back"),
            func.count().filter(and_(unfollow_date_cond, FollowTarget.follow_back == False)).label("not_followed_back"),
        )
        .where(FollowTarget.account_id == account_id)
        .group_by(FollowTarget.source)
        .order_by(func.count().filter(unfollow_date_cond).desc())
    )
    rows = result.all()

    if period == "all":
        earliest = await session.execute(
            select(func.min(FollowTarget.follow_date))
            .where(FollowTarget.account_id == account_id)
        )
        first_date = earliest.scalar()
        days = max((date.today() - first_date).days, 1) if first_date else 1
    else:
        # .get() with a safe default instead of direct-indexing an unvalidated
        # query param — an unrecognized `period` (e.g. ?period=year) used to
        # raise a KeyError -> 500 here.
        days = {"day": 1, "week": 7, "month": 30}.get(period, 7)

    return {
        "days": days,
        "items": [
            {
                "source": row.source,
                "total": row.total,
                "complete": row.complete,
                "followed_back": row.followed_back,
                "not_followed_back": row.not_followed_back,
                "rate": round(row.followed_back / row.complete, 2) if row.complete else None,
            }
            for row in rows
        ],
    }


LOG_SORT_COLUMNS = {
    "date": SessionLog.run_date,
    "run": SessionLog.run_sequence,
    "start": SessionLog.start_time,
    "end": SessionLog.end_time,
    "a1_type": SessionLog.action_1_type,
    "a1_count": SessionLog.action_1_count,
    "a2_type": SessionLog.action_2_type,
    "a2_count": SessionLog.action_2_count,
    "a3_type": SessionLog.action_3_type,
    "a3_count": SessionLog.action_3_count,
    "a4_type": SessionLog.action_4_type,
    "a4_count": SessionLog.action_4_count,
    "error": SessionLog.error_message,
}


@router.get("/{account_id}/log", response_model=dict)
async def get_account_log(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    sort: str = Query("date"),
    sort_dir: str = Query("desc"),
):
    await _get_owned_account(account_id, user, session)
    offset = (page - 1) * page_size

    total = await session.scalar(
        select(func.count()).where(SessionLog.account_id == account_id)
    )

    col = LOG_SORT_COLUMNS.get(sort, SessionLog.run_date)
    order = col.desc().nullslast() if sort_dir == "desc" else col.asc().nullsfirst()

    result = await session.execute(
        select(SessionLog)
        .where(SessionLog.account_id == account_id)
        .order_by(order, SessionLog.run_date.desc(), SessionLog.run_sequence.desc())
        .offset(offset)
        .limit(page_size)
    )
    logs = result.scalars().all()

    def fmt_dt(dt):
        return dt.isoformat() if dt else None

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(lg.id),
                "run_date": lg.run_date.isoformat() if lg.run_date else None,
                "run_sequence": lg.run_sequence,
                "start_time": fmt_dt(lg.start_time),
                "end_time": fmt_dt(lg.end_time),
                "action_1_type": _clean_session_action_type(lg.action_1_type),
                "action_1_count": lg.action_1_count,
                "action_2_type": _clean_session_action_type(lg.action_2_type),
                "action_2_count": lg.action_2_count,
                "action_3_type": _clean_session_action_type(lg.action_3_type),
                "action_3_count": lg.action_3_count,
                "action_4_type": _clean_session_action_type(lg.action_4_type),
                "action_4_count": lg.action_4_count,
                "error_message": lg.error_message,
                "warning_message": lg.warning_message,
            }
            for lg in logs
        ],
    }


ACTIVITY_LOG_SORT_COLUMNS = {
    "created": ActivityLog.created_at,
    "kind": ActivityLog.kind,
    "action": ActivityLog.action,
    "status": ActivityLog.status,
}


@router.get("/{account_id}/activity-log", response_model=dict)
async def get_account_activity_log(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    kind: str | None = Query(None),
    since: datetime | None = Query(None),
    sort: str = Query("created"),
    sort_dir: str = Query("desc"),
):
    """Fine-grained activity/error/failure rows (log_activity / log_error / the
    burnBot_run_log failure uploader). ActivityLog is otherwise write-only —
    this is the one place it can be read back."""
    await _get_owned_account(account_id, user, session)
    offset = (page - 1) * page_size

    filters = [ActivityLog.account_id == account_id]
    if kind:
        filters.append(ActivityLog.kind == kind)
    if since:
        filters.append(ActivityLog.created_at >= since)

    total = await session.scalar(select(func.count()).where(*filters))

    col = ACTIVITY_LOG_SORT_COLUMNS.get(sort, ActivityLog.created_at)
    order = col.desc().nullslast() if sort_dir == "desc" else col.asc().nullsfirst()

    result = await session.execute(
        select(ActivityLog)
        .where(*filters)
        .order_by(order, ActivityLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    logs = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(lg.id),
                "kind": lg.kind,
                "action": lg.action,
                "status": lg.status,
                "details": lg.details,
                "created_at": lg.created_at.isoformat() if lg.created_at else None,
            }
            for lg in logs
        ],
    }


# ── follow seeds ──────────────────────────────────────────────────────────────


@router.get("/{account_id}/seeds", response_model=FollowSeedListRead)
async def get_follow_seeds(
    account_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_owned_account(account_id, user, session)
    return await list_seeds(session, account_id)


@router.post("/{account_id}/seeds", response_model=FollowSeedRead, status_code=status.HTTP_201_CREATED)
async def add_follow_seed(
    account_id: uuid.UUID,
    body: FollowSeedCreate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Add a manual seed; re-adding a retired one reactivates it."""
    await _get_owned_account(account_id, user, session)
    handle = normalize_handle(body.handle)
    if not handle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Handle is required.")
    result = await session.execute(
        select(FollowSeed).where(FollowSeed.account_id == account_id, FollowSeed.handle == handle)
    )
    seed = result.scalar_one_or_none()
    if seed is None:
        seed = FollowSeed(user_id=user.id, account_id=account_id, handle=handle, origin="manual", active=True)
        session.add(seed)
    else:
        seed.active = True
        seed.retired_at = None
        seed.retire_reason = None
    await session.commit()
    await session.refresh(seed)
    stats, _ = await seed_stats_by_handle(session, account_id)
    return seed_to_dict(seed, stats)


@router.patch("/{account_id}/seeds/{seed_id}", response_model=FollowSeedRead)
async def update_follow_seed(
    account_id: uuid.UUID,
    seed_id: uuid.UUID,
    body: FollowSeedUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_owned_account(account_id, user, session)
    result = await session.execute(
        select(FollowSeed).where(FollowSeed.id == seed_id, FollowSeed.account_id == account_id)
    )
    seed = result.scalar_one_or_none()
    if seed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seed not found.")
    if body.active is not None:
        seed.active = body.active
        if body.active:
            seed.retired_at = None
            seed.retire_reason = None
        else:
            seed.retired_at = datetime.now(timezone.utc)
            seed.retire_reason = "disabled by user"
    await session.commit()
    await session.refresh(seed)
    stats, _ = await seed_stats_by_handle(session, account_id)
    return seed_to_dict(seed, stats)


@router.delete("/{account_id}/seeds/{seed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_follow_seed(
    account_id: uuid.UUID,
    seed_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    await _get_owned_account(account_id, user, session)
    result = await session.execute(
        select(FollowSeed).where(FollowSeed.id == seed_id, FollowSeed.account_id == account_id)
    )
    seed = result.scalar_one_or_none()
    if seed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seed not found.")
    await session.delete(seed)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
