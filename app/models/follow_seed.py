import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FollowSeed(Base):
    """A seed account whose followers / similar accounts / post likers the bot
    mines for follow targets. Replaces the comma-separated
    AccountSettings.account_group string so the pool can grow on its own
    (origin = "similar:<parent>") and carry per-seed outcome data.

    Follow-back performance is not stored here — it is derived from
    FollowTarget.source, which embeds the seed handle ("<handle>[followers]",
    "<handle>[likers]", ...). See app/services/follow_seeds.py.
    """

    __tablename__ = "follow_seeds"
    __table_args__ = (UniqueConstraint("account_id", "handle", name="uq_follow_seeds_account_handle"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    handle: Mapped[str] = mapped_column(String(150))  # stored lowercase
    origin: Mapped[str] = mapped_column(String(200), default="manual")  # manual | similar:<parent_handle>
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # % of the seed's pool that was already known on its last use (bot-reported)
    last_saturation: Mapped[int | None] = mapped_column(Integer, nullable=True)
