import uuid
from datetime import datetime, time

from pydantic import BaseModel


class ActionBlock(BaseModel):
    enabled: bool = False
    type: str = ""
    target: str = ""
    fixed_count: int = 0
    variable_count: int = 0


class AccountSettingsUpdate(BaseModel):
    schedule_days: str | None = None
    schedule_start: time | None = None
    schedule_end: time | None = None
    delay_base_minutes: int = 60
    delay_random_minutes: int = 0
    max_runs_per_day: int = 1
    max_runs_random_per_day: int = 0
    actions: list[ActionBlock] | None = None
    actions_random_order: bool = False
    unfollow_days: int = 30
    max_followers: int = 5000
    min_follow_ratio_pct: int = 50
    min_posts: int = 1
    list_tab: str | None = None
    account_group: str | None = None
    account_group_mode: str = "manual"
    account_list_tab: str | None = None
    topics: str | None = None


class AccountSettingsRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    account_id: uuid.UUID
    schedule_days: str | None
    schedule_start: time | None
    schedule_end: time | None
    delay_base_minutes: int
    delay_random_minutes: int
    max_runs_per_day: int
    max_runs_random_per_day: int
    actions: list | None
    actions_random_order: bool
    unfollow_days: int
    max_followers: int = 5000
    min_follow_ratio_pct: int = 50
    min_posts: int = 1
    list_tab: str | None
    account_group: str | None
    account_group_mode: str = "manual"
    account_list_tab: str | None
    topics: str | None
    updated_at: datetime
