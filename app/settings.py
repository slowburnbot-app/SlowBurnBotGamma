from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str
    environment: str = "production"

    # Dedicated key for encrypting stored IG passwords (app/crypto.py). Kept
    # separate from secret_key so a leak of one doesn't also compromise the
    # other — secret_key signs auth JWTs and reset/verification tokens; this
    # key only ever touches encrypted credentials at rest. Optional and
    # falls back to secret_key when unset so existing installs keep
    # decrypting with their current key until an operator deliberately opts
    # in (see scripts/rotate_credential_encryption_key.py to re-encrypt
    # existing rows before/after setting this).
    credential_encryption_key: str = ""

    # Stripe (optional until Phase 3)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_crawl: str = ""
    stripe_price_walk: str = ""
    stripe_price_run: str = ""

    # Base URL of the Next.js frontend — used to build Checkout/Customer
    # Portal success/cancel/return redirect URLs.
    frontend_base_url: str = "https://www.slowburnbot.com"

    # CORS (set once Next.js is deployed)
    cors_origins: list[str] = []

    # Public-facing API base URL (returned to client on activation so bot knows where to call home)
    public_api_url: str = ""

    # GitHub — read bot version from main branch (used on startup and by admin)
    github_token: str = ""
    github_repo: str = ""  # "owner/repo"

    # GHCR — Linux Docker image delivery
    ghcr_namespace: str = ""  # e.g. "ghcr.io/bishopmartin/slowburnbotgamma"

    # Railway S3-compatible object storage — generic release artifact hosting
    bucket_endpoint_url: str = ""   # e.g. "https://<id>.us-east-1.s3.amazonaws.com"
    bucket_name: str = ""
    bucket_access_key_id: str = ""
    bucket_secret_access_key: str = ""
    bucket_region: str = "us-east-1"

    # Desktop build policy
    desktop_activation_token_ttl_hours: int = 24
    desktop_signed_url_expires_seconds: int = 300

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_async_driver(cls, v: str) -> str:
        # Railway injects postgresql:// — SQLAlchemy async needs postgresql+asyncpg://
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
