"""
db/journal.py — ExecutionJournal: персистентный журнал выполнения.

Архитектура взята из arbitr_cockpit (pipeline_engine + PipelineRun + PromptInvocation):

  TaskRun        ←→  PipelineRun       (одна задача = один прогон)
  StepRecord     ←→  pipeline stage    (один вызов инструмента = одна стадия)
  LLMCall        ←→  PromptInvocation  (один вызов Gemini API)

Данные пишутся немедленно после каждого шага (не в конце задачи) —
это обеспечивает crash recovery и replayability (SAD §5 Risk: N°5).

Таблицы:
  journal_runs   — по одной строке на задачу (TaskRun)
  journal_steps  — по одной строке на вызов инструмента (StepRecord)
  journal_llm    — по одной строке на вызов Gemini API (LLMCall)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("journal")


# ── Статусы (mapped from arbitr_cockpit RunStatus) ────────────

class RunStatus(StrEnum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    WAITING  = "waiting_approval"


# ── Data classes ──────────────────────────────────────────────

@dataclass
class LLMCall:
    """Один вызов Gemini API. ←→ PromptInvocation в arbitr_cockpit."""
    run_id:        str
    step_id:       str
    provider:      str  = "gemini"
    model:         str  = "gemini-2.0-flash"
    key_hint:      str  = ""
    tokens_input:  int  = 0
    tokens_output: int  = 0
    latency_ms:    int  = 0
    http_status:   int  = 200
    error:         Optional[str] = None
    invoked_at:    float = field(default_factory=time.time)


@dataclass
class StepRecord:
    """Один вызов инструмента. ←→ pipeline stage в arbitr_cockpit."""
    id:              str
    run_id:          str
    step_idx:        int
    tool_name:       str
    args:            dict
    invocation_id:   str
    idempotency_key: str
    status:          RunStatus = RunStatus.PENDING
    result_json:     Optional[dict] = None
    error_code:      Optional[str]  = None
    retryable:       bool = True
    retry_count:     int  = 0
    duration_ms:     int  = 0
    cached:          bool = False
    started_at:      float = field(default_factory=time.time)
    ended_at:        Optional[float] = None


@dataclass
class TaskRun:
    """Одна задача агента. ←→ PipelineRun в arbitr_cockpit."""
    id:         str
    task_id:    str   # FK → tasks.id
    prompt:     str
    mode:       str   = "NORMAL"
    status:     RunStatus = RunStatus.PENDING
    step_count: int   = 0
    started_at: float = field(default_factory=time.time)
    ended_at:   Optional[float] = None
    snapshot:   Optional[dict] = None   # для crash recovery (SAD §5 Risk N°5)


# ═══════════════════════════════════════════════════════════════
# ExecutionJournal
# ═══════════════════════════════════════════════════════════════

class ExecutionJournal:
    """
    Журнал выполнения задач с немедленной записью каждого шага.
    Обеспечивает replayability и crash recovery.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Журнал прогонов (задач)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS journal_runs (
                    id         TEXT PRIMARY KEY,
                    task_id    TEXT NOT NULL,
                    prompt     TEXT NOT NULL,
                    mode       TEXT DEFAULT 'NORMAL',
                    status     TEXT DEFAULT 'pending',
                    step_count INTEGER DEFAULT 0,
                    snapshot   TEXT,
                    started_at REAL NOT NULL,
                    ended_at   REAL
                )
            """)
            # Журнал шагов (вызовов инструментов)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS journal_steps (
                    id              TEXT PRIMARY KEY,
                    run_id          TEXT NOT NULL,
                    step_idx        INTEGER NOT NULL,
                    tool_name       TEXT NOT NULL,
                    args_json       TEXT NOT NULL,
                    invocation_id   TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status          TEXT DEFAULT 'pending',
                    result_json     TEXT,
                    error_code      TEXT,
                    retryable       INTEGER DEFAULT 1,
                    retry_count     INTEGER DEFAULT 0,
                    duration_ms     INTEGER DEFAULT 0,
                    cached          INTEGER DEFAULT 0,
                    started_at      REAL NOT NULL,
                    ended_at        REAL,
                    FOREIGN KEY (run_id) REFERENCES journal_runs(id)
                )
            """)
            # Журнал LLM вызовов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS journal_llm (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id        TEXT NOT NULL,
                    step_id       TEXT,
                    provider      TEXT DEFAULT 'gemini',
                    model         TEXT DEFAULT 'gemini-2.0-flash',
                    key_hint      TEXT DEFAULT '',
                    tokens_input  INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    latency_ms    INTEGER DEFAULT 0,
                    http_status   INTEGER DEFAULT 200,
                    error         TEXT,
                    invoked_at    REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES journal_runs(id)
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jsteps_run "
                "ON journal_steps(run_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jllm_run "
                "ON journal_llm(run_id)"
            )
            await db.commit()
        logger.info("ExecutionJournal: инициализирован (%s)", self.db_path)

    # ── TaskRun CRUD ─────────────────────────────────────────────

    async def start_run(self, run: TaskRun) -> None:
        """Открываем журнальную запись для новой задачи."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO journal_runs
                (id, task_id, prompt, mode, status, step_count, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run.id, run.task_id, run.prompt, run.mode,
                run.status.value, run.step_count, run.started_at,
            ))
            await db.commit()

    async def finish_run(
        self, run_id: str, status: RunStatus, snapshot: dict | None = None
    ) -> None:
        """Закрываем прогон с финальным статусом."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE journal_runs
                SET status = ?, ended_at = ?, snapshot = ?
                WHERE id = ?
            """, (
                status.value,
                time.time(),
                json.dumps(snapshot, ensure_ascii=False) if snapshot else None,
                run_id,
            ))
            await db.commit()

    async def save_snapshot(self, run_id: str, snapshot: dict) -> None:
        """Обновляем снапшот состояния (crash recovery)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE journal_runs SET snapshot = ? WHERE id = ?",
                (json.dumps(snapshot, ensure_ascii=False), run_id),
            )
            await db.commit()

    # ── StepRecord CRUD ──────────────────────────────────────────

    async def start_step(self, step: StepRecord) -> None:
        """Немедленная запись шага при старте (status=running)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO journal_steps
                (id, run_id, step_idx, tool_name, args_json, invocation_id,
                 idempotency_key, status, retry_count, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                step.id, step.run_id, step.step_idx, step.tool_name,
                json.dumps(step.args, ensure_ascii=False),
                step.invocation_id, step.idempotency_key,
                RunStatus.RUNNING.value, step.retry_count, step.started_at,
            ))
            # Инкрементируем счётчик шагов в прогоне
            await db.execute(
                "UPDATE journal_runs SET step_count = step_count + 1 WHERE id = ?",
                (step.run_id,),
            )
            await db.commit()

    async def finish_step(self, step: StepRecord) -> None:
        """Завершаем шаг с результатом и метриками."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE journal_steps
                SET status = ?, result_json = ?, error_code = ?,
                    retryable = ?, duration_ms = ?, cached = ?,
                    ended_at = ?
                WHERE id = ?
            """, (
                step.status.value,
                json.dumps(step.result_json, ensure_ascii=False) if step.result_json else None,
                step.error_code,
                int(step.retryable),
                step.duration_ms,
                int(step.cached),
                step.ended_at or time.time(),
                step.id,
            ))
            await db.commit()

    # ── LLMCall CRUD ─────────────────────────────────────────────

    async def log_llm_call(self, call: LLMCall) -> None:
        """Записываем вызов LLM API с метриками (tokens, latency, status)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO journal_llm
                (run_id, step_id, provider, model, key_hint,
                 tokens_input, tokens_output, latency_ms, http_status, error, invoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                call.run_id, call.step_id, call.provider, call.model,
                call.key_hint, call.tokens_input, call.tokens_output,
                call.latency_ms, call.http_status, call.error, call.invoked_at,
            ))
            await db.commit()

    # ── Чтение ──────────────────────────────────────────────────

    async def get_run_snapshot(self, run_id: str) -> dict | None:
        """Получить последний снапшот для crash recovery."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT snapshot FROM journal_runs WHERE id = ?", (run_id,)
            ) as cur:
                row = await cur.fetchone()
        if row and row["snapshot"]:
            return json.loads(row["snapshot"])
        return None

    async def get_steps(self, run_id: str) -> list[dict]:
        """Все шаги прогона для отображения в дашборде."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM journal_steps WHERE run_id = ? ORDER BY step_idx",
                (run_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_llm_stats(self, run_id: str) -> dict:
        """Статистика LLM вызовов для задачи."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT
                    COUNT(*)           AS calls,
                    SUM(tokens_input)  AS total_input,
                    SUM(tokens_output) AS total_output,
                    AVG(latency_ms)    AS avg_latency_ms,
                    SUM(CASE WHEN http_status != 200 THEN 1 ELSE 0 END) AS errors
                FROM journal_llm WHERE run_id = ?
            """, (run_id,)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else {}
