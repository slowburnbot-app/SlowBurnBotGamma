import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemConfig(Base):
    """Singleton system-wide configuration (SMTP, TextBelt credentials, etc.)."""

    __tablename__ = "system_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # SMTP settings
    smtp_server: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # TextBelt
    textbelt_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Resend (HTTP email API — replaces SMTP which Railway blocks)
    resend_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    resend_from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resend_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Where public account-request submissions are announced (falls back to
    # resend_reply_to when unset). Plain address, not a secret.
    admin_notify_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Desktop build versioning — auto-updated when a build completes
    current_bot_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_bot_release_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
