"""Application settings.

Everything configurable is read from the environment (12-factor); nothing
sensitive has a usable default. ``SECRET_KEY`` deliberately has no default at
all, so a misconfigured deployment fails at import time rather than signing
tokens with a value that is in the git history.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -----------------------------------------------------
    app_name: str = "TenantForge"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=list)

    # --- database --------------------------------------------------------
    # The *application* connects with a role that has neither SUPERUSER nor
    # BYPASSRLS, so row-level security is not something the application can
    # opt out of. The *admin* URL is the owner role: it runs migrations and
    # the three provisioning operations that exist before a tenant context
    # does (see app/core/db.py).
    database_url: PostgresDsn
    database_admin_url: PostgresDsn
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Role the migration provisions for the application to connect as.
    app_db_role: str = "tenantforge_app"
    app_db_password: str = Field(min_length=8)

    # --- security --------------------------------------------------------
    secret_key: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_ttl_seconds: int = 900  # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days

    # --- rate limiting ---------------------------------------------------
    rate_limit_enabled: bool = True
    login_rate_limit: str = "10/minute"
    register_rate_limit: str = "5/hour"
    refresh_rate_limit: str = "60/minute"

    # --- logging ---------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def async_dsn(self, admin: bool = False) -> str:
        """Return the DSN with the asyncpg driver forced on."""
        raw = str(self.database_admin_url if admin else self.database_url)
        scheme, _, rest = raw.partition("://")
        if "+" not in scheme:
            scheme = f"{scheme}+asyncpg"
        return f"{scheme}://{rest}"

    def sync_dsn(self, admin: bool = True) -> str:
        """Return a psycopg-free sync-shaped DSN (used by Alembic's config)."""
        raw = str(self.database_admin_url if admin else self.database_url)
        scheme, _, rest = raw.partition("://")
        return f"{scheme.split('+')[0]}://{rest}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
