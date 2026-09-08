import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccountRequest(Base):
    """A prospect's "request an account" submission from the public landing page.

    Registration is invite-only; this is the inbound half — the admin reads
    these at /admin/requests and, if interested, issues an invite code.
    """

    __tablename__ = "account_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), index=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instagram_handles: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # new | contacted | invited | declined
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
