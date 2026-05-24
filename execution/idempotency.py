"""
execution/idempotency.py — Реестр операций для идемпотентности.

Любой мутирующий вызов (bash, write_file, git push, http_post) перед
выполнением проверяется здесь. Если операция с таким ключом уже
успешно завершилась — возвращаем кэшированный результат без повторного
выполнения (SAD §3: Idempotency Model).

Idempotency key = sha256(task_id + tool_name + serialised_args)[:32]
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import aiosqlite

logger = logging.getLogger("idempotency")


class OperationRegistry:
    """
    SQLite-backed idempotency registry.
    Записи хранятся 7 дней, затем TTL-cleanup.
    """

    TTL_SECONDS = 7 * 24 * 3600  # 7 дней

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS operation_registry (
                    idempotency_key TEXT PRIMARY KEY,
                    invocation_id   TEXT NOT NULL,
                    task_id         TEXT NOT NULL,
                    tool_name       TEXT NOT NULL,
                    args_hash       TEXT NOT NULL,
                    result_json     TEXT,
                    created_at      REAL NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_op_task "
                "ON operation_registry(task_id)"
            )
            await db.commit()
        logger.info("OperationRegistry: инициализирован (%s)", self.db_path)

    # ── Публичный интерфейс ─────────────────────────────────────

    def make_key(self, task_id: str, tool_name: str, args: dict) -> str:
        """Детерминированный ключ идемпотентности."""
        payload = json.dumps(
            {"task_id": task_id, "tool": tool_name, "args": args},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    async def get_cached(self, idempotency_key: str) -> dict | None:
        """Вернуть кэшированный результат, если операция уже выполнялась."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT result_json, created_at FROM operation_registry "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            return None

        # TTL проверка
        age = time.time() - row["created_at"]
        if age > self.TTL_SECONDS:
            await self._delete(idempotency_key)
            return None

        return json.loads(row["result_json"])

    async def register(
        self,
        idempotency_key: str,
        invocation_id:   str,
        task_id:         str,
        tool_name:       str,
        args:            dict,
        result:          dict,
    ) -> None:
        """Сохранить успешный результат выполнения."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO operation_registry
                (idempotency_key, invocation_id, task_id, tool_name, args_hash, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                idempotency_key,
                invocation_id,
                task_id,
                tool_name,
                hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest(),
                json.dumps(result, ensure_ascii=False),
                time.time(),
            ))
            await db.commit()

    async def cleanup_expired(self) -> int:
        """Удалить устаревшие записи (вызывать периодически)."""
        cutoff = time.time() - self.TTL_SECONDS
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM operation_registry WHERE created_at < ?", (cutoff,)
            )
            await db.commit()
            deleted = cursor.rowcount
        if deleted:
            logger.info("OperationRegistry: очищено %d устаревших записей", deleted)
        return deleted

    # ── Внутренние ──────────────────────────────────────────────

    async def _delete(self, key: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM operation_registry WHERE idempotency_key = ?", (key,)
            )
            await db.commit()
