"""ProjectStore — хранение проектов и шагов в SQLite."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import aiosqlite

from claude_manager.logger import StepLogger

_log = StepLogger("project_store")
DB_PATH = "db/claude_projects.db"


class ProjectStatus(str, Enum):
    PLANNING  = "planning"
    EXECUTING = "executing"
    WAITING   = "waiting"   # ждём сброса лимитов
    PAUSED    = "paused"
    DONE      = "done"
    FAILED    = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


@dataclass
class ProjectStep:
    project_id:   str
    step_index:   int
    description:  str
    status:       StepStatus = StepStatus.PENDING
    result:       str = ""
    error:        str = ""
    account_used: str = ""


@dataclass
class Project:
    project_id:   str
    session_id:   str
    goal:         str
    status:       ProjectStatus
    current_step: int
    total_steps:  int
    created_at:   float
    updated_at:   float
    steps:        list[ProjectStep] = field(default_factory=list)


class ProjectStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init(self) -> None:
        _log.task("инициализация ProjectStore")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id   TEXT PRIMARY KEY,
                    session_id   TEXT NOT NULL DEFAULT '',
                    goal         TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'planning',
                    current_step INTEGER DEFAULT 0,
                    total_steps  INTEGER DEFAULT 0,
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS project_steps (
                    project_id   TEXT NOT NULL,
                    step_index   INTEGER NOT NULL,
                    description  TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    result       TEXT DEFAULT '',
                    error        TEXT DEFAULT '',
                    account_used TEXT DEFAULT '',
                    PRIMARY KEY (project_id, step_index)
                )
            """)
            await db.commit()
        _log.result("ProjectStore готов")
        _log.next("TaskPlanner может создавать проекты")

    async def create_project(self, goal: str, session_id: str = "") -> str:
        pid = str(uuid.uuid4())[:8]
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO projects (project_id,session_id,goal,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (pid, session_id, goal, ProjectStatus.PLANNING, now, now),
            )
            await db.commit()
        _log.result(f"проект создан: {pid}")
        return pid

    async def save_steps(self, project_id: str, steps: list[str]) -> None:
        """Saves decomposed steps and updates total_steps."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            for i, desc in enumerate(steps):
                await db.execute(
                    "INSERT OR REPLACE INTO project_steps (project_id,step_index,description,status) VALUES (?,?,?,?)",
                    (project_id, i, desc, StepStatus.PENDING),
                )
            await db.execute(
                "UPDATE projects SET total_steps=?,status=?,updated_at=? WHERE project_id=?",
                (len(steps), ProjectStatus.EXECUTING, now, project_id),
            )
            await db.commit()

    async def get_project(self, project_id: str) -> Optional[Project]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM projects WHERE project_id=?", (project_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            async with db.execute(
                "SELECT * FROM project_steps WHERE project_id=? ORDER BY step_index",
                (project_id,),
            ) as cur:
                step_rows = await cur.fetchall()
        steps = [
            ProjectStep(
                project_id=r["project_id"], step_index=r["step_index"],
                description=r["description"], status=StepStatus(r["status"]),
                result=r["result"] or "", error=r["error"] or "",
                account_used=r["account_used"] or "",
            )
            for r in step_rows
        ]
        return Project(
            project_id=row["project_id"], session_id=row["session_id"],
            goal=row["goal"], status=ProjectStatus(row["status"]),
            current_step=row["current_step"], total_steps=row["total_steps"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            steps=steps,
        )

    async def list_projects(self, limit: int = 20) -> list[Project]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        result = []
        for row in rows:
            result.append(Project(
                project_id=row["project_id"], session_id=row["session_id"],
                goal=row["goal"], status=ProjectStatus(row["status"]),
                current_step=row["current_step"], total_steps=row["total_steps"],
                created_at=row["created_at"], updated_at=row["updated_at"],
            ))
        return result

    async def update_step(
        self, project_id: str, step_index: int,
        status: StepStatus, result: str = "",
        error: str = "", account_used: str = "",
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE project_steps
                   SET status=?,result=?,error=?,account_used=?
                   WHERE project_id=? AND step_index=?""",
                (status, result, error, account_used, project_id, step_index),
            )
            await db.commit()

    async def advance(
        self, project_id: str, new_status: ProjectStatus, next_step: int
    ) -> None:
        """Update project current_step + status atomically."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE projects SET current_step=?,status=?,updated_at=? WHERE project_id=?",
                (next_step, new_status, time.time(), project_id),
            )
            await db.commit()

    async def set_status(self, project_id: str, status: ProjectStatus) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE projects SET status=?,updated_at=? WHERE project_id=?",
                (status, time.time(), project_id),
            )
            await db.commit()