"""
ContextMemory — персистентная память агента.

Как работает:
  - Каждая задача (промпт + результат) сохраняется в SQLite
  - Перед новым запросом агент автоматически загружает N последних пар (промпт+результат)
  - Тотальный размер разрешен до 100 МБ
  - Старые записи автоматически очищаются, чтобы БД не росла
Использование в агенте:
  context = await memory.build_context_block(limit=10)
  full_prompt = context + "\n\n" + user_prompt
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH  = os.environ.get("CONTEXT_MEMORY_DB", "db/context_memory.db")
MAX_SIZE = 100 * 1024 * 1024  # 100 MB


class ContextMemory:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT    NOT NULL DEFAULT 'default',
                prompt     TEXT    NOT NULL,
                result     TEXT    NOT NULL DEFAULT '',
                ts         REAL    NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_memory_user ON memory(user_id, ts)")
        con.commit()
        con.close()

    # ── Запись ───────────────────────────────────────────────

    def save(self, prompt: str, result: str, user_id: str = "default") -> None:
        """Synchronous — можно вызывать из любого потока."""
        size = len(prompt.encode()) + len(result.encode())
        con  = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO memory (user_id, prompt, result, ts, size_bytes) VALUES (?,?,?,?,?)",
            (user_id, prompt, result, time.time(), size),
        )
        con.commit()
        con.close()
        self._enforce_limit()

    def _enforce_limit(self) -> None:
        """Delete oldest rows if total size > MAX_SIZE."""
        con = sqlite3.connect(self.db_path)
        total = con.execute("SELECT SUM(size_bytes) FROM memory").fetchone()[0] or 0
        while total > MAX_SIZE:
            row = con.execute("SELECT id, size_bytes FROM memory ORDER BY ts ASC LIMIT 1").fetchone()
            if not row:
                break
            con.execute("DELETE FROM memory WHERE id=?", (row[0],))
            con.commit()
            total -= row[1]
        con.close()

    # ── Чтение ──────────────────────────────────────────────

    def build_context_block(self, limit: int = 10, user_id: str = "default") -> str:
        """
        Возвращает блок для представления в начале промпта:

        === Предыдущий контекст (N последних задач) ===
        [Дата] Задача: <prompt>
        Результат: <result>
        ...
        === Конец контекста ===
        """
        con = sqlite3.connect(self.db_path)
        rows = con.execute(
            "SELECT prompt, result, ts FROM memory WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        con.close()

        if not rows:
            return ""

        lines = ["=== Предыдущий контекст ==="]
        for prompt, result, ts in reversed(rows):
            dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
            lines.append(f"[{dt}] Задача: {prompt[:500]}")
            if result:
                lines.append(f"Результат: {result[:1000]}")
            lines.append("")
        lines.append("=== Конец контекста ===")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        con   = sqlite3.connect(self.db_path)
        total = con.execute("SELECT COUNT(*), SUM(size_bytes) FROM memory").fetchone()
        con.close()
        return {"records": total[0] or 0, "size_mb": round((total[1] or 0) / 1024**2, 2)}


# Глобальный синглтон
_memory: Optional[ContextMemory] = None


def get_memory() -> ContextMemory:
    global _memory
    if _memory is None:
        _memory = ContextMemory()
    return _memory