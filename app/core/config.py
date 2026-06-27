"""Application configuration.

All settings are loaded from environment variables (see `.env.example`).
We use `pydantic-settings` so the values are validated and typed, and so an
unknown/missing required value fails fast at startup instead of at request
time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    The two LXD connection modes (`local` via Unix socket, `remote` via mutual
    TLS) are configured together; only the ones relevant to the selected mode
    are actually used.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application ----
    APP_NAME: str = "LXD Management API"
    APP_ENV: Literal["development", "production"] = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    # Comma-separated origins; "*" disables the origin check.
    CORS_ORIGINS: str = "*"

    # ---- LXD connection ----
    LXD_CONNECTION_MODE: Literal["local", "remote"] = "local"
    LXD_SOCKET_PATH: str = "/var/snap/lxd/common/lxd/unix.socket"
    LXD_REMOTE_URL: str = "https://lxd-host:8443"
    LXD_CLIENT_CERT_PATH: str = ""
    LXD_CLIENT_KEY_PATH: str = ""
    LXD_TRUSTED_CA_PATH: str = ""
    LXD_TIMEOUT: float = 30.0

    # ---- Database (SQLite for the local user store) ----
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/lxd_api.db"

    # ---- JWT (auth for clients of THIS API) ----
    JWT_SECRET: str = "change-me-to-a-long-random-secret-value"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ---- First-run admin seeding ----
    SEED_ADMIN_USERNAME: str = ""
    SEED_ADMIN_PASSWORD: str = ""

    @field_validator("LXD_REMOTE_URL")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins parsed into a list (handles the `"*"` wildcard)."""
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_local_mode(self) -> bool:
        """True when connecting to LXD over the Unix socket."""
        return self.LXD_CONNECTION_MODE == "local"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton `Settings` instance.

    Cached so we read `.env` / process env exactly once per process.
    """
    return Settings()


settings = get_settings()
