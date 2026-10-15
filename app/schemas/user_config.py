import uuid
from datetime import datetime

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, field_validator


class UserConfigUpdate(BaseModel):
    like_suggested: bool | None = None
    like_sponsored: bool | None = None
    skip_login_check: bool | None = None
    login_tries: int | None = None
    skip_private: bool | None = None
    unfollow_days: int | None = None
    max_followers: int | None = None
    min_follow_ratio_pct: int | None = None
    min_posts: int | None = None
    notices_type: str = "none"
    notices_session: bool = True
    notices_login: bool = True
    login_notices_type: str = "email"
    login_notify_email: str | None = None
    login_notify_phone: str | None = None
    notify_email: str | None = None
    notify_phone: str | None = None
    bot_debug: bool | None = None
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown timezone")
        return v


class UserConfigRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    like_suggested: bool
    like_sponsored: bool
    skip_login_check: bool
    login_tries: int
    skip_private: bool
    unfollow_days: int = 30
    max_followers: int = 5000
    min_follow_ratio_pct: int = 50
    min_posts: int = 1
    notices_type: str
    notices_session: bool
    notices_login: bool
    login_notices_type: str
    login_notify_email: str | None
    login_notify_phone: str | None
    notify_email: str | None
    notify_phone: str | None
    bot_debug: bool
    timezone: str | None = None
    updated_at: datetime
