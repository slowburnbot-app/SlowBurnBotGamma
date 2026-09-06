"""Schemas for Instagram action-limit events (bot-reported) and the active
cooldowns derived from them."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

ACTION_PATTERN = r"^(like|follow|unfollow|all)$"
TIER_PATTERN = r"^(soft|hard|status_page)$"


class ActionLimitCreate(BaseModel):
    account_id: uuid.UUID
    action: str = Field(pattern=ACTION_PATTERN)
    tier: str = Field(pattern=TIER_PATTERN)
    reason: str = Field(default="", max_length=200)
    details: str | None = Field(default=None, max_length=8000)


class ActiveActionLimit(BaseModel):
    """The cooldown currently in force for one action verb on an account."""
    model_config = {"from_attributes": True}

    action: str
    tier: str
    reason: str
    strike: int
    until: datetime | None
    created_at: datetime


class ActionLimitRead(ActiveActionLimit):
    """History row (dashboard detail page)."""
    id: uuid.UUID
    cleared_at: datetime | None = None
    cleared_reason: str | None = None


class StatusPageCheckCreate(BaseModel):
    account_id: uuid.UUID
    clean: bool | None   # None = page could not be read (still stamps the check time)
    text: str | None = Field(default=None, max_length=8000)


class ReleasedLimit(BaseModel):
    action: str
    until: datetime


class StatusPageCheckResult(BaseModel):
    ok: bool = True
    released: list[ReleasedLimit] = []
