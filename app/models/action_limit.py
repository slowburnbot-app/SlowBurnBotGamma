import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ActionLimit(Base):
    """One Instagram action-limit event reported by the bot client.

    `tier` "hard" (block dialog / rejected request) rows carry a cooldown
    `until`; "soft" (silent flip-then-revert) and "status_page" rows are
    history only. The active cooldown for (account, action) is the newest
    hard row whose `until` is still ahead and `cleared_at` is unset — see
    app/services/action_limits.py, which also computes the 7-day strike
    escalation from these rows.
    """

    __tablename__ = "action_limits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(20))   # like | follow | unfollow | all
    tier: Mapped[str] = mapped_column(String(20))     # soft | hard | status_page
    reason: Mapped[str] = mapped_column(String(200), default="")
    strike: Mapped[int] = mapped_column(Integer, default=1)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    account: Mapped["Account"] = relationship(back_populates="action_limit_events")
