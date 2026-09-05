import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DesktopBuildConfig(BaseModel):
    """Customer-configurable settings stored per client slot.

    Browser/driver settings (chrome_path, headless, user_agent, etc.) are no longer
    stored here — they are written to the [browser] section of burnBot_config.ini at
    activation time using hardcoded defaults in the bot client.
    """

    client_name: str = Field(default="", max_length=15)
    system_type: Literal["windows", "linux", "macos"] = "windows"
    novnc_url: str = Field(default="http://localhost:6080/vnc.html#autoconnect=1&resize=scale", max_length=200)


class DesktopBuildCreate(BaseModel):
    config: DesktopBuildConfig
    slot_number: int | None = None  # if set, use this as client_id (rebuild preserving slot)


class DesktopBuildRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    client_id: int
    status: str
    build_options: dict
    failure_reason: str | None = None
    bot_version: str | None = None
    activated_at: datetime | None = None
    consumed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DesktopBuildWithToken(DesktopBuildRead):
    """Returned only on initial create/rebuild — includes the one-time activation token."""

    activation_token: str


class DesktopActivateRequest(BaseModel):
    user_id: uuid.UUID
    client_id: int
    activation_token: str = Field(..., min_length=1, max_length=200)
    bot_version: str = Field(default="", max_length=50)
