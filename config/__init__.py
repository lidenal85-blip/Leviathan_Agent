"""
config/settings.py — настройки LEVIATHAN AGENT
"""
from __future__ import annotations
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Gemini
    gemini_keys: str = ""

    # Telegram
    tg_bot_token: str = ""
    tg_admin_chat_id: int = 0

    # GitHub
    github_token: str = ""
    github_username: str = ""

    # Сервер
    host: str = "0.0.0.0"
    port: int = 8200
    secret_key: str = "change_me"

    # БД
    db_path: str = "db/agent.db"

    # Безопасность
    default_mode: str = "NORMAL"

    # Пути
    workspace: str = "/var/www"
    leviathan_engine: str = "/opt/leviathan_engine"

    # Лимиты
    max_iterations: int = 50
    tool_timeout_sec: int = 60
    max_file_size_kb: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def gemini_keys_list(self) -> list[str]:
        return [k.strip() for k in self.gemini_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
