"""
config/settings.py — единая точка конфигурации LEVIATHAN AGENT.
Читает .env в корне проекта. Образец: .env.example
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── База данных ─────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///db/leviathan.db"

    # ── Gemini ключи (до 14) ────────────────────────────────────
    GEMINI_K1:  str = ""
    GEMINI_K2:  str = ""
    GEMINI_K3:  str = ""
    GEMINI_K4:  str = ""
    GEMINI_K5:  str = ""
    GEMINI_K6:  str = ""
    GEMINI_K7:  str = ""
    GEMINI_K8:  str = ""
    GEMINI_K9:  str = ""
    GEMINI_K10: str = ""
    GEMINI_K11: str = ""
    GEMINI_K12: str = ""
    GEMINI_K13: str = ""
    GEMINI_K14: str = ""

    # ── Groq ключи (до 5) ───────────────────────────────────────
    GROQ_K1: str = ""
    GROQ_K2: str = ""
    GROQ_K3: str = ""
    GROQ_K4: str = ""
    GROQ_K5: str = ""

    # ── Telegram ────────────────────────────────────────────────
    TG_BOT_TOKEN:    str = ""
    TG_ADMIN_CHAT_ID: int = 0

    # ── GitHub ──────────────────────────────────────────────────
    GITHUB_TOKEN: str = ""

    # ── Агент ───────────────────────────────────────────────────
    MAX_ITERATIONS:   int = 50
    DEFAULT_MODE:     str = "NORMAL"       # SAFE | NORMAL | FULL
    TOOL_TIMEOUT_SEC: int = 60
    MAX_FILE_SIZE_KB: int = 100

    # ── Web ─────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8200

    # ── Модель ──────────────────────────────────────────────────
    GEMINI_MODEL: str = "gemini-2.5-flash"


    # ── Claude / Anthropic ──────────────────────────────────────
    ANTHROPIC_API_KEY:      str = ""
    CLAUDE_MODEL:           str = "claude-sonnet-4-5"
    CLAUDE_THINKING_BUDGET: int = 10_000

    # ── Model Router ────────────────────────────────────────────
    MODEL_MODE: str = "AUTO"  # AUTO|GEMINI_ONLY|CLAUDE_ONLY|GEMINI_THINK_CLAUDE|CLAUDE_THINK_GEMINI

    # ── ArbitrCockpit ───────────────────────────────────────────
    ARBITR_URL: str = "http://localhost:8090"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    # ── Производные свойства ─────────────────────────────────────

    @property
    def gemini_keys_list(self) -> list[str]:
        """Все непустые Gemini ключи в порядке номера."""
        keys = []
        for i in range(1, 15):
            k = getattr(self, f"GEMINI_K{i}", "")
            if k.strip():
                keys.append(k.strip())
        return keys

    @property
    def db_path(self) -> str:
        return self.DATABASE_URL.replace("sqlite+aiosqlite:///", "")

    @property
    def tg_configured(self) -> bool:
        return bool(self.TG_BOT_TOKEN and self.TG_ADMIN_CHAT_ID)


    @property
    def model_mode(self) -> str:
        return self.MODEL_MODE

    @property
    def anthropic_api_key(self) -> str:
        return self.ANTHROPIC_API_KEY

    @property
    def claude_model(self) -> str:
        return self.CLAUDE_MODEL

    @property
    def claude_thinking_budget(self) -> int:
        return self.CLAUDE_THINKING_BUDGET


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
