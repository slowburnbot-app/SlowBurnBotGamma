import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FollowTarget(Base):
    """Per-account follow/unfollow database — mirrors per-account sheet tabs."""

    __tablename__ = "follow_targets"
    # One row per (account, handle), case-insensitive. Created by alembic e7f8a9b0c1d2.
    __table_args__ = (Index("uq_follow_targets_account_handle", "account_id", text("lower(target_handle)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    target_handle: Mapped[str] = mapped_column(String(150))
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "bucket[followers]"
    # Vocabulary set by the bot client:
    #   following  — bot has followed this handle, awaiting unfollow
    #   done       — bot has unfollowed (terminal)
    #   skipped    — bot chose not to follow (terminal)
    status: Mapped[str] = mapped_column(String(50), default="following")
    follow_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    unfollow_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    follow_back: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="follow_targets")
