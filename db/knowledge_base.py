"""
db/knowledge_base.py — База знаний агента.

Архитектура:
  knowledge_entries  — опыт по каждой задаче (резюме, файлы, выводы)
  file_index         — индекс всех файлов созданных агентом
  kb_compressions    — архив сжатых периодов (актив пользователя)

Логика компрессии:
  Когда entries > COMPRESS_THRESHOLD → Gemini сжимает в мета-резюме
  → Сохраняется в kb_compressions (постоянно, не удаляется)
  → Текущие entries очищаются, цикл начинается заново
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger("knowledge_base")

COMPRESS_THRESHOLD = 200   # записей до компрессии
CONTEXT_ENTRIES    = 8     # записей в контекст промта
MAX_CONTEXT_CHARS  = 3000  # лимит символов контекста


class KnowledgeBase:

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Опыт по задачам
            await db.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id        TEXT NOT NULL,
                    summary        TEXT NOT NULL,
                    files          TEXT DEFAULT '[]',
                    tools_used     TEXT DEFAULT '[]',
                    outcome        TEXT DEFAULT 'done',
                    tags           TEXT DEFAULT '[]',
                    compression_id INTEGER DEFAULT NULL,
                    created_at     REAL NOT NULL
                )
            """)
            # Индекс файлов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS file_index (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     TEXT NOT NULL,
                    path        TEXT NOT NULL UNIQUE,
                    file_type   TEXT DEFAULT 'file',
                    size_bytes  INTEGER DEFAULT 0,
                    description TEXT DEFAULT '',
                    created_at  REAL NOT NULL
                )
            """)
            # Архив компрессий
            await db.execute("""
                CREATE TABLE IF NOT EXISTS kb_compressions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_from  REAL NOT NULL,
                    period_to    REAL NOT NULL,
                    entry_count  INTEGER NOT NULL,
                    meta_summary TEXT NOT NULL,
                    created_at   REAL NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fi_path ON file_index(path)"
            )
            await db.commit()
        logger.info("KnowledgeBase: инициализирована (%s)", self.db_path)

    # ── Запись опыта ─────────────────────────────────────────────

    async def save_entry(
        self,
        task_id:    str,
        summary:    str,
        files:      list[str] | None = None,
        tools_used: list[str] | None = None,
        outcome:    str = "done",
        tags:       list[str] | None = None,
    ) -> int:
        """Сохранить опыт задачи в базу знаний."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("""
                INSERT INTO knowledge_entries
                (task_id, summary, files, tools_used, outcome, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                task_id,
                summary[:2000],
                json.dumps(files or [], ensure_ascii=False),
                json.dumps(tools_used or [], ensure_ascii=False),
                outcome,
                json.dumps(tags or [], ensure_ascii=False),
                time.time(),
            ))
            await db.commit()
            entry_id = cur.lastrowid
        logger.info("KnowledgeBase: запись #%d для задачи %s", entry_id, task_id)
        return entry_id

    async def index_file(
        self,
        task_id:     str,
        path:        str,
        file_type:   str = "file",
        description: str = "",
    ) -> None:
        """Добавить файл в индекс."""
        size = 0
        try:
            size = Path(path).stat().st_size
        except Exception:
            pass
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO file_index
                (task_id, path, file_type, size_bytes, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_id, path, file_type, size, description[:200], time.time()))
            await db.commit()

    # ── Чтение контекста ─────────────────────────────────────────

    async def get_context(self) -> str:
        """
        Контекст для системного промта: последние N задач + сжатое резюме.
        Возвращает строку которая вставляется в промт агента.
        """
        parts = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Последнее сжатое резюме
            async with db.execute(
                "SELECT meta_summary, period_from, period_to, entry_count "
                "FROM kb_compressions ORDER BY id DESC LIMIT 1"
            ) as cur:
                comp = await cur.fetchone()

            if comp:
                d_from = datetime.fromtimestamp(comp["period_from"]).strftime("%d.%m")
                d_to   = datetime.fromtimestamp(comp["period_to"]).strftime("%d.%m")
                parts.append(
                    f"[АРХИВ ОПЫТА {d_from}–{d_to}, {comp['entry_count']} задач]\n"
                    f"{comp['meta_summary'][:800]}"
                )

            # Свежие записи
            async with db.execute("""
                SELECT task_id, summary, files, outcome, created_at
                FROM knowledge_entries
                ORDER BY id DESC LIMIT ?
            """, (CONTEXT_ENTRIES,)) as cur:
                rows = await cur.fetchall()

        if rows:
            parts.append("[НЕДАВНИЕ ЗАДАЧИ]")
            for r in reversed(rows):
                dt    = datetime.fromtimestamp(r["created_at"]).strftime("%d.%m %H:%M")
                files = json.loads(r["files"] or "[]")
                f_str = f" → файлы: {', '.join(files)}" if files else ""
                parts.append(
                    f"• [{dt}] #{r['task_id'][:8]} [{r['outcome']}]{f_str}\n"
                    f"  {r['summary'][:200]}"
                )

        if not parts:
            return ""

        ctx = "\n".join(parts)
        if len(ctx) > MAX_CONTEXT_CHARS:
            ctx = ctx[:MAX_CONTEXT_CHARS] + "\n...[обрезано]"
        return ctx

    async def find_file(self, query: str) -> list[dict]:
        """Найти файлы по описанию или расширению."""
        q = f"%{query}%"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT path, file_type, description, task_id, created_at
                FROM file_index
                WHERE path LIKE ? OR description LIKE ? OR file_type LIKE ?
                ORDER BY created_at DESC LIMIT 5
            """, (q, q, q)) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Полнотекстовый поиск по резюме задач."""
        q = f"%{query}%"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT task_id, summary, files, outcome, created_at
                FROM knowledge_entries
                WHERE summary LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """, (q, limit)) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Компрессия ────────────────────────────────────────────────

    async def needs_compression(self) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM knowledge_entries WHERE compression_id IS NULL"
            ) as cur:
                count = (await cur.fetchone())[0]
        return count >= COMPRESS_THRESHOLD

    async def compress(self, meta_summary: str) -> int:
        """
        Сжать текущие записи в мета-резюме.
        Вызывается агентом когда needs_compression() = True.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT MIN(created_at), MAX(created_at), COUNT(*) "
                "FROM knowledge_entries WHERE compression_id IS NULL"
            ) as cur:
                period_from, period_to, count = await cur.fetchone()

            cur2 = await db.execute("""
                INSERT INTO kb_compressions
                (period_from, period_to, entry_count, meta_summary, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (period_from, period_to, count, meta_summary, time.time()))
            comp_id = cur2.lastrowid

            await db.execute(
                "UPDATE knowledge_entries SET compression_id = ? "
                "WHERE compression_id IS NULL",
                (comp_id,),
            )
            await db.commit()

        logger.info("KnowledgeBase: сжато %d записей → компрессия #%d", count, comp_id)
        return comp_id

    async def stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM knowledge_entries") as cur:
                total_entries = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM file_index") as cur:
                total_files = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM kb_compressions") as cur:
                total_compressions = (await cur.fetchone())[0]
        return {
            "entries":           total_entries,
            "files":             total_files,
            "compressions":      total_compressions,
            "needs_compression": total_entries >= COMPRESS_THRESHOLD,
        }
