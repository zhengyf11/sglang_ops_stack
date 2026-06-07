from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SGLANG_OPS_", env_file=".env", extra="ignore")

    app_name: str = "sglang_ops_stack"
    database_url: str = "sqlite:///./sglang_ops_stack.db"
    ssh_connect_timeout: float = Field(default=10.0, gt=0)
    ssh_command_timeout: float = Field(default=30.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
