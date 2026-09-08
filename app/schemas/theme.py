import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE24_KEYS = [
    "base00", "base01", "base02", "base03", "base04", "base05", "base06", "base07",
    "base08", "base09", "base0A", "base0B", "base0C", "base0D", "base0E", "base0F",
    "base10", "base11", "base12", "base13", "base14", "base15", "base16", "base17",
]
_CANONICAL = {k.lower(): k for k in BASE24_KEYS}
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


class ThemeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    variant: Literal["dark", "light"]
    author: str | None = Field(default=None, max_length=300)
    palette: dict[str, str]

    @field_validator("palette")
    @classmethod
    def _strict_base24(cls, value: dict[str, str]) -> dict[str, str]:
        """Exactly the 24 base24 slots, each a 6-digit hex.

        The palette is emitted into a <style> tag on the client, so nothing
        that isn't a hex color may be stored. Key case is canonicalized
        (base0a -> base0A) and values normalized to "#rrggbb" lowercase.
        """
        out: dict[str, str] = {}
        for raw_key, raw_val in value.items():
            key = _CANONICAL.get(str(raw_key).lower())
            if key is None:
                raise ValueError(f"unknown palette slot {raw_key!r}")
            m = _HEX.match(str(raw_val).strip())
            if m is None:
                raise ValueError(f"palette.{key} must be a 6-digit hex color (got {raw_val!r})")
            out[key] = "#" + m.group(1).lower()
        missing = [k for k in BASE24_KEYS if k not in out]
        if missing:
            raise ValueError(
                f"palette is missing {', '.join(missing)} — is this a base24 scheme?"
            )
        return {k: out[k] for k in BASE24_KEYS}


class ThemeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    variant: str
    author: str | None
    palette: dict[str, str]
    created_at: datetime
