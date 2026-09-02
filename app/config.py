from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Classroom Companion"
    environment: str = "development"
    database_url: str = "sqlite:///./classroom_companion.db"
    session_secret: str = "development-only-change-me"
    base_url: str = "http://127.0.0.1:8000"
    school_timezone: str = "Asia/Kolkata"
    llm_mode: str = "fake"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    telegram_mode: str = "log"
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str = "change-me"
    upload_dir: Path = Path("uploads")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    quiet_hour_start: int = Field(default=21, ge=0, le=23)
    quiet_hour_end: int = Field(default=7, ge=0, le=23)
    reminder_interval_seconds: int = Field(default=300, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
