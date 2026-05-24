"""
core_bridge/key_pool.py — Пул Gemini API ключей.

Приоритеты при запуске:
  1. Пробуем импортировать KeyPool из /opt/leviathan_engine/core/key_pool.py
     (production: агент развёрнут внутри engine)
  2. Fallback: используем bundled GeminiKeyPool (async, round-robin + backoff).

Оба варианта предоставляют единый async-интерфейс через KeyPoolAdapter:
  - await adapter.get_key()      → str (API ключ)
  - adapter.mark_ok(key)         → сброс счётчика ошибок
  - adapter.mark_rate_limited(key) → backoff на ключ
  - adapter.stats()              → list[dict]
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("key_pool")


# ═══════════════════════════════════════════════════════════════
# BUNDLED: GeminiKeyPool (async, self-contained)
# Источник: agent_draft/key_pool (1).py — принят как эталон
# ═══════════════════════════════════════════════════════════════

@dataclass
class KeyState:
    key:           str
    failures:      int   = 0
    blocked_until: float = 0.0
    requests:      int   = 0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.blocked_until

    def block(self, seconds: float) -> None:
        self.blocked_until = time.time() + seconds
        self.failures += 1

    def reset(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0


class GeminiKeyPool:
    """
    Async-пул Gemini API ключей.
    Round-robin с экспоненциальным backoff при 429.
    Используется как основа KeyPoolAdapter.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("KeyPool: нужен хотя бы один ключ")
        self._keys = [KeyState(k) for k in keys]
        self._idx  = 0
        self._lock = asyncio.Lock()
        logger.info("GeminiKeyPool: инициализирован с %d ключами", len(keys))

    async def get_key(self) -> str:
        """Возвращает доступный ключ (async-safe)."""
        async with self._lock:
            for _ in range(len(self._keys)):
                state = self._keys[self._idx % len(self._keys)]
                self._idx += 1
                if state.is_available:
                    state.requests += 1
                    return state.key

            # Все заблокированы — ждём ближайшего
            soonest = min(self._keys, key=lambda s: s.blocked_until)
            wait = max(0.0, soonest.blocked_until - time.time())
            logger.warning("GeminiKeyPool: все ключи заблокированы, ждём %.1fs", wait)
            await asyncio.sleep(wait)
            soonest.requests += 1
            return soonest.key

    def mark_rate_limited(self, key: str) -> None:
        """Помечаем ключ как 429 — блокируем с exponential backoff."""
        for state in self._keys:
            if state.key == key:
                backoff = min(2 ** state.failures, 300)
                state.block(backoff)
                logger.warning(
                    "GeminiKeyPool: ключ ...%s заблокирован на %ds (попытка %d)",
                    key[-6:], backoff, state.failures,
                )
                return

    def mark_ok(self, key: str) -> None:
        """Сбрасываем счётчик ошибок после успеха."""
        for state in self._keys:
            if state.key == key:
                state.reset()
                return

    def stats(self) -> list[dict]:
        return [
            {
                "key":         f"...{s.key[-6:]}",
                "requests":    s.requests,
                "failures":    s.failures,
                "available":   s.is_available,
                "blocked_for": max(0.0, s.blocked_until - time.time()),
            }
            for s in self._keys
        ]

    @property
    def available_count(self) -> int:
        return sum(1 for s in self._keys if s.is_available)


# ═══════════════════════════════════════════════════════════════
# BRIDGE: пробуем подключиться к engine core
# ═══════════════════════════════════════════════════════════════

class _CoreKeyPoolAdapter:
    """
    Обёртка над core.key_pool.KeyPool (sync) для async-интерфейса.
    Используется когда агент запущен внутри /opt/leviathan_engine/.
    """

    def __init__(self, core_pool) -> None:
        self._pool = core_pool
        logger.info("CoreKeyPoolAdapter: используем KeyPool из leviathan_engine/core")

    async def get_key(self) -> str:
        loop = asyncio.get_event_loop()
        entry = await loop.run_in_executor(
            None, lambda: self._pool.get_key("gemini")
        )
        if entry is None:
            raise RuntimeError("CoreKeyPool: все ключи исчерпаны")
        return entry.value

    def mark_rate_limited(self, key: str) -> None:
        for entries in self._pool._entries.values():
            for e in entries:
                if e.value == key:
                    self._pool.report(e, 429)
                    return

    def mark_ok(self, key: str) -> None:
        for entries in self._pool._entries.values():
            for e in entries:
                if e.value == key:
                    self._pool.report(e, 200)
                    return

    def stats(self) -> list[dict]:
        status = self._pool.status()
        result = []
        for provider, info in status.items():
            result.append({
                "provider":    provider,
                "total":       info["total"],
                "available":   info["available"],
                "in_cooldown": info["in_cooldown"],
            })
        return result

    @property
    def available_count(self) -> int:
        total = 0
        for entries in self._pool._entries.values():
            total += sum(1 for e in entries if e.is_available)
        return total


def build_key_pool(keys: list[str]) -> GeminiKeyPool | _CoreKeyPoolAdapter:
    """
    Фабрика: пробует core engine, fallback на bundled GeminiKeyPool.
    """
    try:
        import sys
        import os
        engine_path = os.environ.get("LEVIATHAN_ENGINE_PATH", "/opt/leviathan_engine")
        if engine_path not in sys.path:
            sys.path.insert(0, engine_path)

        from core.key_pool import get_pool  # type: ignore
        core_pool = get_pool()
        if core_pool.active_count("gemini") > 0:
            return _CoreKeyPoolAdapter(core_pool)  # type: ignore
        logger.warning("CoreKeyPool: нет активных Gemini ключей, используем bundled GeminiKeyPool")
    except ImportError:
        logger.info("CoreKeyPool: engine недоступен, используем bundled GeminiKeyPool")
    except Exception as exc:
        logger.warning("CoreKeyPool: ошибка инициализации (%s), fallback", exc)

    if not keys:
        raise ValueError(
            "Нет Gemini ключей. Добавьте GEMINI_K1..K14 в .env"
        )
    return GeminiKeyPool(keys)
