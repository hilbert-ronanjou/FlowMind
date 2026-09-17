from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FlowMind AI Cloud API"
    api_v1_prefix: str = "/api/v1"
    database_url: str
    jwt_secret: str = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=1440, gt=0)
    frontend_url: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

