"""
agent/key_pool.py — ротация Gemini API ключей
Round-robin с экспоненциальным backoff при 429.
"""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class KeyState:
    key: str
    failures: int = 0
    blocked_until: float = 0.0
    requests: int = 0

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
    Пул Gemini API ключей с ротацией и backoff.
    При 429 — блокируем ключ на 2^n секунд и берём следующий.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("KeyPool: нужен хотя бы один ключ")
        self._keys = [KeyState(k) for k in keys]
        self._idx = 0
        self._lock = asyncio.Lock()
        logger.info("KeyPool: инициализирован с %d ключами", len(keys))

    async def get_key(self) -> str:
        """Возвращает доступный ключ."""
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
            logger.warning("KeyPool: все ключи заблокированы, ждём %.1fs", wait)
            await asyncio.sleep(wait)
            soonest.requests += 1
            return soonest.key

    def mark_rate_limited(self, key: str) -> None:
        """Помечаем ключ как 429 — блокируем с backoff."""
        for state in self._keys:
            if state.key == key:
                backoff = min(2 ** state.failures, 300)
                state.block(backoff)
                logger.warning(
                    "KeyPool: ключ ...%s заблокирован на %ds (попытка %d)",
                    key[-6:], backoff, state.failures
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
                "key": f"...{s.key[-6:]}",
                "requests": s.requests,
                "failures": s.failures,
                "available": s.is_available,
                "blocked_for": max(0.0, s.blocked_until - time.time()),
            }
            for s in self._keys
        ]
