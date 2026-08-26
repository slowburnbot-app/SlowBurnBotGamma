import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserConfig(Base):
    """User-wide configuration (notification preferences, etc.)."""

    __tablename__ = "user_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )

    # Session settings
    like_suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    like_sponsored: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_login_check: Mapped[bool] = mapped_column(Boolean, default=False)
    login_tries: Mapped[int] = mapped_column(Integer, default=3)

    # Follow settings
    skip_private: Mapped[bool] = mapped_column(Boolean, default=False)

    # Notification settings
    notices_type: Mapped[str] = mapped_column(String(10), default="email")  # text/email/both/none
    notices_session: Mapped[bool] = mapped_column(Boolean, default=True)
    notices_login: Mapped[bool] = mapped_column(Boolean, default=True)
    login_notices_type: Mapped[str] = mapped_column(String(10), default="email")
    login_notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    login_notify_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    vnc_pin: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Verbose bot-client debug logging, flippable from the dashboard without
    # a container restart (see bot-client/burnBot_run_log.py + is_bot_debug_enabled()).
    bot_debug: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="config")
