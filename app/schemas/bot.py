"""Schemas for exe-facing bot endpoints."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.account import AccountRead
from app.schemas.account_settings import AccountSettingsRead


class SessionLogCreate(BaseModel):
    account_id: uuid.UUID
    run_date: date
    run_sequence: int = 1
    start_time: datetime | None = None
    end_time: datetime | None = None
    action_1_type: str | None = None
    action_1_count: int = 0
    action_2_type: str | None = None
    action_2_count: int = 0
    action_3_type: str | None = None
    action_3_count: int = 0
    action_4_type: str | None = None
    action_4_count: int = 0
    error_message: str | None = None
    warning_message: str | None = None


class ActivityLogCreate(BaseModel):
    account_id: uuid.UUID
    kind: str  # "activity" | "error"
    action: str | None = None
    status: str | None = None
    details: str | None = None


class EntitlementRead(BaseModel):
    active: bool
    plan_tier: str
    current_period_end: datetime | None = None


class CredentialsRead(BaseModel):
    ig_password: str | None = None


class IgnoreHandlesRead(BaseModel):
    handles: list[str]


class FollowTargetCreate(BaseModel):
    account_id: uuid.UUID
    target_handle: str
    source: str | None = None
    status: str = "following"
    follow_date: date | None = None


class FollowTargetUpdate(BaseModel):
    status: str | None = None
    unfollow_date: date | None = None
    follow_back: bool | None = None


class FollowTargetRead(BaseModel):
    id: uuid.UUID
    target_handle: str
    source: str | None
    status: str
    follow_date: date | None
    unfollow_date: date | None
    follow_back: bool | None


class RunCountRead(BaseModel):
    count: int


class BotUserConfigRead(BaseModel):
    like_suggested: bool = False
    like_sponsored: bool = False
    skip_login_check: bool = False
    login_tries: int = 3
    skip_private: bool = False
    notices_type: str
    notices_session: bool
    notify_email: str | None = None
    notify_phone: str | None = None
    notices_login: bool = True
    login_notices_type: str = "email"
    login_notify_email: str | None = None
    login_notify_phone: str | None = None
    vnc_pin: str | None = None
    bot_debug: bool = False


class BotUserConfigUpdate(BaseModel):
    """Partial update for the notification prefs the TUI /settings screen edits."""
    notices_session: bool | None = None
    notices_type: str | None = None
    notices_login: bool | None = None
    login_notices_type: str | None = None
    bot_debug: bool | None = None


class ClientAccountState(AccountRead):
    settings: AccountSettingsRead | None = None


class ClientStateRead(BaseModel):
    version: str
    changed: bool
    entitlement: EntitlementRead
    current_bot_version: str | None = None
    user_config: BotUserConfigRead | None = None
    accounts: list[ClientAccountState] | None = None


class BotNotifyRequest(BaseModel):
    channel: str = Field(pattern=r"^(email|sms)$")
    to: str = Field(min_length=1, max_length=320)
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class HeartbeatCreate(BaseModel):
    client_id: int
    system_type: str = ""
    ip_address: str = ""
    status: str = "idle"
    current_account: str | None = None
    bot_version: str = ""


class ClientStatusRead(BaseModel):
    client_id: int
    system_type: str
    ip_address: str
    status: str
    current_account: str | None
    last_heartbeat: datetime
    connected: bool
