"""
db/storage.py — хранение задач в SQLite.
Расширение оригинала из agent_draft/storage.py:
  + invocation_id на каждый шаг (SAD §7 рекомендация №1)
  + немедленная запись после каждого шага (не только в конце задачи)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from agent.core import Task, TaskStatus, TaskStep

logger = logging.getLogger("storage")


class TaskStorage:
    def __init__(self, db_path: str = "db/leviathan.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id           TEXT PRIMARY KEY,
                    prompt       TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    result       TEXT DEFAULT '',
                    error        TEXT DEFAULT '',
                    steps_json   TEXT DEFAULT '[]',
                    mode         TEXT DEFAULT 'NORMAL',
                    created_at   REAL NOT NULL,
                    finished_at  REAL DEFAULT 0
                )
            """)
            # Phase 1: добавляем колонки если ещё нет (ALTER TABLE безопасно)
            for col, defval in [
                ("paused_at",    "REAL DEFAULT 0"),
                ("pause_reason", "TEXT DEFAULT ''"),
                ("current_step", "INTEGER DEFAULT 0"),
                ("fire_and_forget", "INTEGER DEFAULT 0"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defval}")
                except Exception:
                    pass  # колонка уже есть
            await db.commit()
        logger.info("TaskStorage: инициализирован (%s)", self.db_path)

    async def save(self, task: Task) -> None:
        steps_data = [
            {
                "idx":            s.idx,
                "tool":           s.tool,
                "args":           s.args,
                "result":         s.result,
                "invocation_id":  s.invocation_id,
                "idempotency_key": s.idempotency_key,
                "ts":             s.ts,
                "duration":       s.duration,
            }
            for s in task.steps
        ]
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO tasks
                (id, prompt, status, result, error, steps_json, mode, created_at, finished_at,
                 paused_at, pause_reason, current_step, fire_and_forget)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.id, task.prompt, task.status.value,
                task.result, task.error,
                json.dumps(steps_data, ensure_ascii=False),
                task.mode, task.created_at, task.finished_at,
                task.paused_at, task.pause_reason,
                task.current_step, int(task.fire_and_forget),
            ))
            await db.commit()

    async def get(self, task_id: str) -> Task | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    async def list_recent(self, limit: int = 20) -> list[Task]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row) -> Task:
        task = Task(
            id=row["id"],
            prompt=row["prompt"],
            status=TaskStatus(row["status"]),
            result=row["result"] or "",
            error=row["error"] or "",
            mode=row["mode"] or "NORMAL",
            created_at=row["created_at"],
            finished_at=row["finished_at"] or 0.0,
            paused_at=row["paused_at"] if "paused_at" in row.keys() else 0.0,
            pause_reason=row["pause_reason"] if "pause_reason" in row.keys() else "",
            current_step=row["current_step"] if "current_step" in row.keys() else 0,
            fire_and_forget=bool(row["fire_and_forget"]) if "fire_and_forget" in row.keys() else False,
        )
        steps_data = json.loads(row["steps_json"] or "[]")
        task.steps = [
            TaskStep(
                idx=s["idx"],
                tool=s["tool"],
                args=s["args"],
                result=s.get("result"),
                invocation_id=s.get("invocation_id", ""),
                idempotency_key=s.get("idempotency_key", ""),
                ts=s["ts"],
                duration=s["duration"],
            )
            for s in steps_data
        ]
        return task

    async def get_paused_tasks(self) -> list:
        """Возвращает все задачи со статусом PAUSED или RUNNING (для hot-resume)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE status IN ('paused', 'running') ORDER BY created_at"
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_task(r) for r in rows]
