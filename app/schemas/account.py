import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.action_limit import ActiveActionLimit


class AccountCreate(BaseModel):
    name: str
    enabled: bool = True
    group_number: int | None = None
    ig_password: str | None = None
    proxy_enabled: bool = False
    proxy_type: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    group_number: int | None = None
    ig_password: str | None = None
    proxy_enabled: bool | None = None
    proxy_type: str | None = None


class AccountRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    enabled: bool
    system_disabled: bool = False
    group_number: int | None
    has_password: bool = False
    proxy_enabled: bool
    proxy_type: str | None
    # Active Instagram action-limit cooldowns (attached by the routers from
    # app.services.action_limits.active_limits — not a column).
    action_limits: list[ActiveActionLimit] = []
    status_page_checked_at: datetime | None = None
    status_page_result: str | None = None
    created_at: datetime
    updated_at: datetime
