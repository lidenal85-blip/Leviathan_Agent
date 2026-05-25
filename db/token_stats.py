"""
db/token_stats.py — Учёт токенов по задачам и ключам.
"""
import time
import json
import logging
from pathlib import Path
import aiosqlite

logger = logging.getLogger("token_stats")
_db_path = None


def init_token_stats(db_path: str):
    global _db_path
    _db_path = db_path


async def _ensure_table():
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                key_alias   TEXT NOT NULL,
                model       TEXT NOT NULL,
                task_id     TEXT NOT NULL,
                tokens_in   INTEGER DEFAULT 0,
                tokens_out  INTEGER DEFAULT 0,
                created_at  REAL NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tu_date ON token_usage(date)"
        )
        await db.commit()


async def log_usage(key_alias: str, model: str, task_id: str,
                    tokens_in: int, tokens_out: int):
    if not _db_path:
        return
    try:
        await _ensure_table()
        date = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(_db_path) as db:
            await db.execute("""
                INSERT INTO token_usage
                (date, key_alias, model, task_id, tokens_in, tokens_out, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (date, key_alias, model, task_id, tokens_in, tokens_out, time.time()))
            await db.commit()
    except Exception as e:
        logger.warning("token_stats log_usage error: %s", e)


async def get_stats(days: int = 7) -> dict:
    if not _db_path:
        return {}
    try:
        await _ensure_table()
        cutoff = time.strftime(
            "%Y-%m-%d",
            time.localtime(time.time() - days * 86400)
        )
        async with aiosqlite.connect(_db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT date,
                       SUM(tokens_in)  as tin,
                       SUM(tokens_out) as tout,
                       COUNT(*)        as calls
                FROM token_usage
                WHERE date >= ?
                GROUP BY date ORDER BY date DESC
            """, (cutoff,)) as cur:
                by_day = [dict(r) for r in await cur.fetchall()]

            async with db.execute("""
                SELECT key_alias,
                       SUM(tokens_in)  as tin,
                       SUM(tokens_out) as tout,
                       COUNT(*)        as calls
                FROM token_usage
                WHERE date >= ?
                GROUP BY key_alias ORDER BY tout DESC
            """, (cutoff,)) as cur:
                by_key = [dict(r) for r in await cur.fetchall()]

            async with db.execute("""
                SELECT SUM(tokens_in) as tin, SUM(tokens_out) as tout, COUNT(*) as calls
                FROM token_usage WHERE date = ?
            """, (time.strftime("%Y-%m-%d"),)) as cur:
                today = dict(await cur.fetchone() or {})

        return {"today": today, "by_day": by_day, "by_key": by_key}
    except Exception as e:
        logger.warning("token_stats get_stats error: %s", e)
        return {}


async def get_session_summary(task_id: str) -> str:
    if not _db_path:
        return ""
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path) as db:
            async with db.execute("""
                SELECT SUM(tokens_in) as tin, SUM(tokens_out) as tout,
                       COUNT(*) as calls, model
                FROM token_usage WHERE task_id = ?
                GROUP BY model
            """, (task_id,)) as cur:
                rows = await cur.fetchall()
        if not rows:
            return ""
        parts = []
        for r in rows:
            tin, tout, calls, model = r
            parts.append(
                f"  {model}: {calls} вызов(а), "
                f"↑{tin or 0} + ↓{tout or 0} = {(tin or 0)+(tout or 0)} токенов"
            )
        return "📊 Токены:\n" + "\n".join(parts)
    except Exception as e:
        return ""
