from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SGLANG_OPS_", env_file=".env", extra="ignore")

    app_name: str = "sglang_ops_stack"
    environment: str = "development"
    database_url: str = "sqlite:///./sglang_ops_stack.db"
    docs_enabled: bool = True
    log_level: str = "INFO"
    log_format: str = "json"
    security_headers: tuple[str, ...] = ("x-content-type-options", "x-frame-options")
    require_https: bool = False
    ssh_connect_timeout: float = Field(default=10.0, gt=0)
    ssh_command_timeout: float = Field(default=30.0, gt=0)
    auth_secret_key: str = Field(default="dev-only-change-me", min_length=16)
    secret_key: str = Field(default="dev-only-secret-key", min_length=16)
    access_token_expire_minutes: int = Field(default=60, gt=0)
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    task_queue_mode: str = Field(default="background", pattern="^(background|celery)$")
    db_pool_size: int = Field(default=5, gt=0)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout: int = Field(default=30, gt=0)
    db_pool_recycle: int = Field(default=1800, gt=0)

    @field_validator("security_headers", mode="before")
    @classmethod
    def parse_security_headers(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, list | tuple):
            return tuple(str(item) for item in value)
        return tuple()


@lru_cache
def get_settings() -> Settings:
    return Settings()
