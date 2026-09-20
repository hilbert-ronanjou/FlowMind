from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FlowMind AI Cloud API"
    api_v1_prefix: str = "/api/v1"
    database_url: str
    jwt_secret: str = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=1440, gt=0)
    frontend_url: str = "http://localhost:3000"
    dashscope_api_key: str | None = None
    dashscope_base_url: str | None = None
    qwen_model: str | None = None
    embedding_model: Literal["text-embedding-v4"] = "text-embedding-v4"
    embedding_dimensions: Literal[1024] = 1024
    document_storage_root: Path = Path("../storage/documents")
    document_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    document_course_limit: int = Field(default=20, gt=0)
    document_chunk_size_chars: int = Field(default=1200, ge=100)
    document_chunk_overlap_chars: int = Field(default=200, ge=0)
    embedding_batch_size: int = Field(default=16, gt=0, le=128)
    document_processing_stale_seconds: int = Field(default=1800, gt=0)

    @model_validator(mode="after")
    def validate_document_chunking(self) -> Self:
        if self.document_chunk_overlap_chars >= self.document_chunk_size_chars:
            raise ValueError("document chunk overlap must be smaller than chunk size")
        return self

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
