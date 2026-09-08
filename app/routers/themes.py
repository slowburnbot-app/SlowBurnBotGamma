"""Public read side of the theme catalog.

No auth on purpose: the theme provider re-applies a visitor's stored choice on
every page, including the public landing page where there is no bearer token,
and a palette is not sensitive. Mutations live in the admin router.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.theme import Theme
from app.schemas.theme import ThemeRead

router = APIRouter(prefix="/themes", tags=["themes"])


@router.get("", response_model=list[ThemeRead])
async def list_themes(session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(select(Theme).order_by(Theme.name))
    return result.scalars().all()


@router.get("/{slug}", response_model=ThemeRead)
async def get_theme(slug: str, session: AsyncSession = Depends(get_async_session)):
    theme = await session.scalar(select(Theme).where(Theme.slug == slug))
    if theme is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Theme not found")
    return theme
