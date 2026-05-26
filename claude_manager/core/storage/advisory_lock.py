"""SQLite advisory lock — без Redis, без threading.Lock.

Gибрид:
  1. asyncio.Lock   — защита от concurrent coroutines в одном process
  2. SQLite row      — защита от concurrent processes (будущее)

Пример:
    async with AdvisoryLock(db_path, account_id, timeout=30):
        await do_rotation()
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict

import aiosqlite

from claude_manager.logger import StepLogger

_log = StepLogger("advisory_lock")

LOCK_TTL    = 60    # сек — TTL для SQLite-записи
RETRY_SLEEP = 0.3   # сек между попыткам

CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS rotation_locks (
        lock_key   TEXT PRIMARY KEY,
        locked_at  REAL NOT NULL
    )
"""

# Модульный реестр asyncio.Lock по имени лока
_locks: Dict[str, asyncio.Lock] = {}
_registry_mu: asyncio.Lock = None  # type: ignore  # инициализация ниже


def _get_asyncio_lock(key: str) -> asyncio.Lock:
    """asyncio.Lock создаётся лениво внутри текущего event loop."""
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


class LockAcquireError(Exception):
    """Лок не удалось получить за timeout секунд."""


class AdvisoryLock:
    """Async context manager для advisory lock.

    Использует asyncio.Lock для надёжной защиты внутри процесса
    + SQLite row для видимости на дашборде.
    """

    def __init__(self, db_path: str, lock_key: str, timeout: float = 30.0):
        self.db_path   = db_path
        self.lock_key  = lock_key
        self.timeout   = timeout
        self._alock    = _get_asyncio_lock(lock_key)
        self._acquired = False

    async def __aenter__(self) -> "AdvisoryLock":
        await self._acquire()
        return self

    async def __aexit__(self, *_) -> None:
        await self._release()

    # ── internal ────────────────────────────────────────────────────

    async def _acquire(self) -> None:
        _log.step(f"попытка захватить лок '{self.lock_key}'")
        try:
            await asyncio.wait_for(self._alock.acquire(), timeout=self.timeout)
        except asyncio.TimeoutError:
            _log.warn(f"лок '{self.lock_key}' не получен за {self.timeout}s")
            raise LockAcquireError(f"lock '{self.lock_key}' timeout after {self.timeout}s")

        self._acquired = True
        _log.step(f"лок '{self.lock_key}' захвачен")
        # запись в SQLite для операционной видимости (дашборд)
        await self._sqlite_mark(locked=True)

    async def _release(self) -> None:
        if not self._acquired:
            return
        await self._sqlite_mark(locked=False)
        self._alock.release()
        self._acquired = False
        _log.step(f"лок '{self.lock_key}' освобождён")

    async def _sqlite_mark(self, locked: bool) -> None:
        """SQLite-запись для операционной видимости. Не критична."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(CREATE_SQL)
                if locked:
                    await db.execute(
                        "INSERT OR REPLACE INTO rotation_locks (lock_key, locked_at) VALUES (?, ?)",
                        (self.lock_key, time.time()),
                    )
                else:
                    await db.execute(
                        "DELETE FROM rotation_locks WHERE lock_key=?",
                        (self.lock_key,),
                    )
                await db.commit()
        except Exception as exc:
            _log.warn(f"_sqlite_mark locked={locked}: {exc}")