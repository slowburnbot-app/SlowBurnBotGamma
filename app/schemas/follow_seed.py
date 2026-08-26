"""Schemas for follow seeds (dashboard + bot)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FollowSeedRead(BaseModel):
    id: uuid.UUID
    handle: str
    origin: str
    active: bool
    added_at: datetime
    retired_at: datetime | None = None
    retire_reason: str | None = None
    last_used_at: datetime | None = None
    last_saturation: int | None = None
    # Derived from follow_targets whose source starts with "<handle>[" (all-time)
    total: int = 0
    complete: int = 0
    followed_back: int = 0
    rate: float | None = None


class FollowSeedListRead(BaseModel):
    items: list[FollowSeedRead]
    active_count: int
    # Follow-back rate across every completed target of the account (all sources)
    account_rate: float | None = None


class FollowSeedCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=150)


class FollowSeedUpdate(BaseModel):
    active: bool | None = None


class BotFollowSeedCreate(BaseModel):
    account_id: uuid.UUID
    handle: str = Field(min_length=1, max_length=150)
    origin: str = Field(default="manual", max_length=200)


class BotFollowSeedUpdate(BaseModel):
    last_used_at: datetime | None = None
    last_saturation: int | None = None
    active: bool | None = None
    retire_reason: str | None = Field(default=None, max_length=200)
