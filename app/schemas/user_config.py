import uuid
from datetime import datetime

from pydantic import BaseModel


class UserConfigUpdate(BaseModel):
    like_suggested: bool | None = None
    like_sponsored: bool | None = None
    skip_login_check: bool | None = None
    login_tries: int | None = None
    skip_private: bool | None = None
    notices_type: str = "none"
    notices_session: bool = True
    notices_login: bool = True
    login_notices_type: str = "email"
    login_notify_email: str | None = None
    login_notify_phone: str | None = None
    notify_email: str | None = None
    notify_phone: str | None = None
    bot_debug: bool | None = None


class UserConfigRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    like_suggested: bool
    like_sponsored: bool
    skip_login_check: bool
    login_tries: int
    skip_private: bool
    notices_type: str
    notices_session: bool
    notices_login: bool
    login_notices_type: str
    login_notify_email: str | None
    login_notify_phone: str | None
    notify_email: str | None
    notify_phone: str | None
    bot_debug: bool
    updated_at: datetime
