import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AccountSettings(Base):
    """Schedule and action configuration for a bot account.

    actions: JSON array of up to 4 action blocks, each:
      {"enabled": bool, "type": str, "target": str, "fixed_count": int, "variable_count": int}
    """

    __tablename__ = "account_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Schedule
    schedule_days: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "daily"
    schedule_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    schedule_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    delay_base_minutes: Mapped[int] = mapped_column(Integer, default=60)
    delay_random_minutes: Mapped[int] = mapped_column(Integer, default=0)
    max_runs_per_day: Mapped[int] = mapped_column(Integer, default=1)
    max_runs_random_per_day: Mapped[int] = mapped_column(Integer, default=0)

    # Actions (up to 4, stored as JSONB)
    actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    actions_random_order: Mapped[bool] = mapped_column(Boolean, default=False)

    # Unfollow / follow sources
    unfollow_days: Mapped[int] = mapped_column(Integer, default=30)
    # Follow candidate filters read off the profile hover card before a follow (0 = rule off):
    # skip accounts with more than max_followers, require following/followers >=
    # min_follow_ratio_pct percent, require at least min_posts posts.
    max_followers: Mapped[int] = mapped_column(Integer, default=5000)
    min_follow_ratio_pct: Mapped[int] = mapped_column(Integer, default=50)
    min_posts: Mapped[int] = mapped_column(Integer, default=1)
    list_tab: Mapped[str | None] = mapped_column(String(150), nullable=True)
    account_group: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Where "account list …" (incl. "account list [likers]") take their targets from:
    #   manual — the account_group text above, random pick (the original behaviour)
    #   pool   — the follow_seeds table: follow-back-weighted pick, auto-retire,
    #            auto-discovery (bootstrapped from account_group when the pool is empty)
    account_group_mode: Mapped[str] = mapped_column(String(10), default="manual")
    account_list_tab: Mapped[str | None] = mapped_column(String(150), nullable=True)
    topics: Mapped[str | None] = mapped_column(String(500), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="settings")
