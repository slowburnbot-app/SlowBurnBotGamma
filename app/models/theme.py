import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# The SSR baseline (frontend/themes/slowburnbot.yaml) — its row is seeded by
# the themes migration and can never be deleted, so a visitor whose stored
# choice disappears always has something to fall back to.
DEFAULT_THEME_SLUG = "slowburnbot"


class Theme(Base):
    """A base24 color scheme offered to users on /dashboard/account.

    Admins curate the catalog at /admin/themes (add from the bundled gallery or
    a pasted scheme YAML, delete). The palette is rendered into a <style> tag
    on the client, so the create schema only admits strict #rrggbb values.
    """

    __tablename__ = "themes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # dark | light
    variant: Mapped[str] = mapped_column(String(5))
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # {"base00": "#rrggbb", ..., "base17": "#rrggbb"} — all 24 slots, lowercase
    palette: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
