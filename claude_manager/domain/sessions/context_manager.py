"""SessionContextManager — виртуальные сессии, история диалога."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

import aiosqlite

from claude_manager.logger import StepLogger

_log = StepLogger("sessions")

DB_PATH     = "db/claude_sessions.db"
MAX_HISTORY = 100   # макс сообщений на сессию
SESSION_TTL = 86400  # 24 часа без активности


@dataclass
class SessionMapping:
    session_id:      str
    user_id:         str
    provider:        str          # claude | gemini
    account_id:      str
    conversation_id: str          # реальный ID в Claude API
    message_count:   int
    created_at:      float
    last_used:       float


@dataclass
class Message:
    seq:       int
    role:      str   # user | assistant
    content:   str
    ts:        float


class SessionContextManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # ── Инициализация ──────────────────────────────────────────

    async def init(self) -> None:
        _log.task("инициализация SessionContextManager")
        _log.step("создание таблиц sessions + messages")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id      TEXT PRIMARY KEY,
                    user_id         TEXT NOT NULL,
                    provider        TEXT NOT NULL DEFAULT 'claude',
                    account_id      TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    message_count   INTEGER DEFAULT 0,
                    created_at      REAL NOT NULL,
                    last_used       REAL NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT    NOT NULL,
                    role       TEXT    NOT NULL,
                    content    TEXT    NOT NULL,
                    ts         REAL    NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
            )
            await db.commit()
        _log.result("SessionContextManager готов")
        _log.next("LLMProviderPool будет создавать сессии")

    # ── CRUD сессий ─────────────────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        provider: str = "claude",
        account_id: str = "",
        conversation_id: str = "",
    ) -> str:
        _log.task(f"создание сессии для user={user_id}")
        session_id = str(uuid.uuid4())
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO sessions
                   (session_id,user_id,provider,account_id,conversation_id,created_at,last_used)
                   VALUES (?,?,?,?,?,?,?)""",
                (session_id, user_id, provider, account_id, conversation_id, now, now),
            )
            await db.commit()
        _log.result(f"сессия создана: {session_id[:8]}... user={user_id}")
        return session_id

    async def get_mapping(self, session_id: str) -> Optional[SessionMapping]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return SessionMapping(
            session_id=row["session_id"],
            user_id=row["user_id"],
            provider=row["provider"],
            account_id=row["account_id"],
            conversation_id=row["conversation_id"],
            message_count=row["message_count"],
            created_at=row["created_at"],
            last_used=row["last_used"],
        )

    async def update_mapping(
        self,
        session_id: str,
        account_id: str,
        conversation_id: str,
        provider: str = "claude",
    ) -> None:
        """Called after account switch / migration."""
        _log.task(f"обновление маппинга сессии {session_id[:8]}")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE sessions
                   SET provider=?,account_id=?,conversation_id=?,last_used=?
                   WHERE session_id=?""",
                (provider, account_id, conversation_id, time.time(), session_id),
            )
            await db.commit()
        _log.result(f"маппинг обновлён: acc={account_id} conv={conversation_id[:8]}...")

    # ── История сообщений ───────────────────────────────────────

    async def append_message(
        self, session_id: str, role: str, content: str
    ) -> None:
        """Append-only. Если превышен MAX_HISTORY — удаляем старые."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (session_id,role,content,ts) VALUES (?,?,?,?)",
                (session_id, role, content, now),
            )
            # бъём количество сообщений
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ) as cur:
                count = (await cur.fetchone())[0]
            # если превышаем лимит — чистим самые старые
            if count > MAX_HISTORY:
                await db.execute(
                    """DELETE FROM messages WHERE session_id=? AND id IN (
                        SELECT id FROM messages WHERE session_id=?
                        ORDER BY id ASC LIMIT ?
                    )""",
                    (session_id, session_id, count - MAX_HISTORY),
                )
                _log.warn(f"сессия {session_id[:8]}: truncated to {MAX_HISTORY} messages")
            # обновляем счётчик и last_used
            await db.execute(
                """UPDATE sessions
                   SET message_count=?,last_used=? WHERE session_id=?""",
                (min(count, MAX_HISTORY), now, session_id),
            )
            await db.commit()

    async def get_history(
        self, session_id: str, limit: int = 20
    ) -> list[Message]:
        """Последние `limit` сообщений в хронологическом порядке."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id,role,content,ts FROM messages
                   WHERE session_id=?
                   ORDER BY id DESC LIMIT ?""",
                (session_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        # DESC → разворачиваем, чтобы порядок был хронологическим
        return [
            Message(seq=r["id"], role=r["role"], content=r["content"], ts=r["ts"])
            for r in reversed(rows)
        ]

    # ── Очистка устаревших сессий ─────────────────────────────

    async def cleanup_expired(self, ttl: int = SESSION_TTL) -> int:
        """Returns кол-во удалённых сессий."""
        _log.task("очистка устаревших сессий")
        cutoff = time.time() - ttl
        async with aiosqlite.connect(self.db_path) as db:
            # удаляем историю
            await db.execute(
                """DELETE FROM messages WHERE session_id IN (
                    SELECT session_id FROM sessions WHERE last_used < ?
                )""",
                (cutoff,),
            )
            cur = await db.execute(
                "DELETE FROM sessions WHERE last_used < ?", (cutoff,)
            )
            await db.commit()
            deleted = cur.rowcount
        _log.result(f"удалено {deleted} устаревших сессий")
        _log.next("следующая очистка через 24ч")
        return deleted

    async def get_stats(self) -> dict:
        """Counts for dashboard."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM sessions") as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM sessions WHERE last_used > ?",
                (time.time() - 3600,),
            ) as cur:
                active_1h = (await cur.fetchone())[0]
        return {"total": total, "active_1h": active_1h}