from datetime import datetime

from pydantic import BaseModel


class NotificationCredentialsUpdate(BaseModel):
    smtp_server: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    textbelt_key: str | None = None
    resend_api_key: str | None = None
    resend_from_address: str | None = None
    resend_reply_to: str | None = None
    admin_notify_email: str | None = None


class NotificationCredentialsRead(BaseModel):
    smtp_server: str
    smtp_port: int
    smtp_user: str | None
    smtp_password_set: bool
    textbelt_key_set: bool
    resend_api_key_set: bool
    resend_from_address: str | None
    resend_reply_to: str | None
    admin_notify_email: str | None
    updated_at: datetime
