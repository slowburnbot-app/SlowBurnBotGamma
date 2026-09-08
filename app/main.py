import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, select

from app.auth import ACCESS_TOKEN_LIFETIME, auth_backend, fastapi_users
from app.database import async_session_maker
from app.models.access_token import AccessToken
from app.models.system_config import SystemConfig
from app.routers import accounts, admin, auth_refresh, bot, config, desktop_builds, public, subscription, webhooks
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services import github_actions, object_storage
from app.settings import settings

logging.basicConfig(level=logging.INFO)


async def _prune_expired_access_tokens() -> None:
    """DatabaseStrategy (app/auth.py) never deletes a token row once it ages
    past ACCESS_TOKEN_LIFETIME — read_token just starts filtering it out via
    max_age. Without this, access_tokens grows without bound. Best-effort:
    failures here shouldn't block startup."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACCESS_TOKEN_LIFETIME)
        async with async_session_maker() as session:
            await session.execute(delete(AccessToken).where(AccessToken.created_at < cutoff))
            await session.commit()
    except Exception as e:
        logging.warning(f"Startup access-token cleanup failed: {e}")


async def _sync_bot_version() -> None:
    """On startup, advance current_bot_version only if both build artifacts exist.

    Mirrors POST /admin/sync-bot-version: the gate is the Windows EXE plus the
    Docker image; the macOS binary is not required (see that endpoint).
    """
    if not settings.github_token or not settings.github_repo:
        return
    try:
        version = await github_actions.get_main_branch_bot_version()
        if not version:
            return
        exe_key = f"releases/windows/SlowBurnBot-{version}.exe"
        exe_ready = object_storage.object_exists(exe_key)
        image_ready = await github_actions.ghcr_image_has_tag(version)
        if not (exe_ready and image_ready):
            logging.info(
                f"Startup bot sync skipped — artifacts not yet ready for {version} "
                f"(exe={exe_ready}, image={image_ready})"
            )
            return
        async with async_session_maker() as session:
            sc = await session.scalar(select(SystemConfig))
            if sc is None:
                sc = SystemConfig()
                session.add(sc)
            def _gt(a: str, b: str) -> bool:
                try:
                    return [int(x) for x in a.split(".")] > [int(x) for x in b.split(".")]
                except Exception:
                    return False
            if sc.current_bot_version is None or _gt(version, sc.current_bot_version):
                sc.current_bot_version = version
                sc.current_bot_release_date = datetime.now(timezone.utc).strftime("%b %d %Y")
                await session.commit()
                logging.info(f"Bot version synced to {version}")
    except Exception as e:
        logging.warning(f"Startup bot version sync failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _sync_bot_version()
    await _prune_expired_access_tokens()
    yield


app = FastAPI(
    title="SlowBurnBot API",
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes: /auth/jwt/login, /auth/jwt/logout
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

# User management: /auth/register, /users/me, /users/{id}
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)

# App routers
app.include_router(auth_refresh.router)
app.include_router(accounts.router)
app.include_router(bot.router)
app.include_router(config.router)
app.include_router(admin.router)
app.include_router(subscription.router)
app.include_router(webhooks.router)
app.include_router(desktop_builds.router)
app.include_router(public.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
