"""
agent/core.py — LEVIATHAN AGENT v3.1
Gemini function calling loop + Claude integration via ModelRouter.

Изменения v3.1:
  - LeviathanAgent принимает claude_adapter + model_router
  - Task.model_mode — per-task режим
  - CLAUDE_ONLY / CLAUDE_THINK_GEMINI: Claude планирует, Gemini исполняет
  - GEMINI_THINK_CLAUDE: claude_think доступен как Gemini-инструмент
  - model_name по умолчанию → gemini-2.0-flash
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

import google.generativeai as genai

if TYPE_CHECKING:
    from core_bridge.key_pool import GeminiKeyPool
    from core_bridge.claude_adapter import ClaudeAdapter
    from agent.model_router import ModelRouter
    from db.journal import ExecutionJournal, LLMCall, StepRecord, TaskRun
    from execution.idempotency import OperationRegistry
    from execution.result_envelope import ResultEnvelope

from agent.tools import TOOLS_REGISTRY, GEMINI_TOOLS, is_dangerous
from execution.result_envelope import (
    MUTABLE_TOOLS, ResultEnvelope, ResultStatus, ErrorCode
)

logger = logging.getLogger("agent.core")

# ── Системный промт ───────────────────────────────────────────
SYSTEM_PROMPT = """Ты — LEVIATHAN AGENT v3.1, автономный DevOps + Arbitr агент.

═══ СЕРВЕРНАЯ ЭКОСИСТЕМА ═══
- VoiceStudio:    /var/www/voicestudio    (port 8120) — аудио обработка
- KinoVibe:       /var/www/kinovibe       (port 8110) — фильм-матчер
- AI Outreach:    /opt/ai_outreach        (port 8000) — outreach система
- Orionyx:        /opt/orionyx            (port 8005) — инвестиционная платформа
- LEVIATHAN:      /opt/leviathan_agent    (port 8200) — этот агент
- ArbitrCockpit:  /opt/arbitr_cockpit     (port 8090) — конвейер AI-ролей
- GitHub:         github.com/lidenal85-blip

═══ РЕЖИМЫ РАБОТЫ ═══
SAFE   — только read_file, list_dir, http_get (никаких изменений)
NORMAL — всё кроме rm -rf, DROP TABLE, systemctl stop без подтверждения
FULL   — полные права включая деструктивные операции и git push

═══ ПРАВИЛА РАБОТЫ ═══
1. Перед изменением файла — ВСЕГДА read_file сначала
2. После изменений — curl health check сервиса
3. Логируй каждый шаг: 🔍 Читаю / ✏️ Пишу / ✅ Готово / ❌ Ошибка
4. Финальный отчёт: что сделано, файлы изменены, ссылки
5. Git push только в FULL режиме или с явного разрешения

═══ ARBITR WORKFLOW ═══
Для оценки и ведения заказов используй:
1. arbitr_lisa_estimate    — TC-оценка сложности (автономно, без сети)
2. arbitr_pipeline_status  — статус конвейера заказа
3. arbitr_pipeline_start   — запустить стадию (triage/architect/developer...)
4. arbitr_submit_response  — отправить ответ в стадию

═══ РЕЖИМЫ РОЛИ ═══
Если задача начинается с [DECOMPOSER] — действуй как системный декомпозитор:
  Выдай модули, dependency graph, порядок разработки, контракты.
  Правила: одна ответственность, явные контракты, нет god-modules.

Если задача начинается с [ARCHITECT] — действуй как архитектор (Senior/Staff):
  Выдай ADR для каждого решения: Context→Decision→Alternatives→Trade-offs→Consequences.
  Структура: System Overview, Module Architecture, Integration, Risks, Evolution Path.
  НЕ пиши код — только архитектура.

Если задача начинается с [AUDITOR] — действуй как архитектурный аудитор:
  Проверяй, не проектируй. Severity: Critical/High/Medium/Low.
  Проверяй: Domain Integrity, Data Flow, Integration Safety, Failure Scenarios, Security, Observability.
  Вердикт: READY / READY WITH FIXES / NOT READY.

═══ ЕСЛИ GEMINI НЕДОСТУПЕН ═══
Используй инструмент claude_think для сложных аналитических задач.
"""


# ── Статусы задачи ────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    WAITING   = "waiting_approval"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


# ── Шаг задачи ───────────────────────────────────────────────

@dataclass
class TaskStep:
    idx:             int
    tool:            str
    args:            dict
    invocation_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""
    result:          Optional[dict] = None
    ts:              float = field(default_factory=time.time)
    duration:        float = 0.0
    provider:        str = "gemini"   # "gemini" | "claude"


# ── Задача ────────────────────────────────────────────────────

@dataclass
class Task:
    id:               str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt:           str = ""
    status:           TaskStatus = TaskStatus.PENDING
    steps:            list[TaskStep] = field(default_factory=list)
    result:           str = ""
    error:            str = ""
    created_at:       float = field(default_factory=time.time)
    finished_at:      float = 0.0
    mode:             str = "NORMAL"
    model_mode:       Optional[str] = None   # None = использовать default из настроек
    pending_approval: Optional[dict] = None
    journal_run_id:   str = field(default_factory=lambda: str(uuid.uuid4()))


# ═══════════════════════════════════════════════════════════════
# LeviathanAgent
# ═══════════════════════════════════════════════════════════════

class LeviathanAgent:
    """
    Основной агент v3.1. Gemini FC-loop + Claude integration.

    Параметры:
      key_pool       — GeminiKeyPool
      journal        — ExecutionJournal
      registry       — OperationRegistry
      on_step        — callback (task, step)
      on_approval_needed — callback (task, cmd) → bool
      model_name     — Gemini модель (по умолчанию gemini-2.0-flash)
      claude_adapter — ClaudeAdapter (опционально)
      model_router   — ModelRouter (опционально, по умолчанию GEMINI_ONLY)
    """

    def __init__(
        self,
        key_pool,
        max_iterations:      int = 50,
        journal:             Optional["ExecutionJournal"] = None,
        registry:            Optional["OperationRegistry"] = None,
        on_step:             Optional[Callable] = None,
        on_approval_needed:  Optional[Callable] = None,
        model_name:          str = "gemini-2.0-flash",
        claude_adapter:      Optional["ClaudeAdapter"] = None,
        model_router:        Optional["ModelRouter"] = None,
    ) -> None:
        self.key_pool           = key_pool
        self.max_iterations     = max_iterations
        self.journal            = journal
        self.registry           = registry
        self.on_step            = on_step
        self.on_approval_needed = on_approval_needed
        self.model_name         = model_name
        self.claude_adapter     = claude_adapter
        self.model_router       = model_router

    def _build_model(self, key: str) -> genai.GenerativeModel:
        genai.configure(api_key=key)
        return genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=SYSTEM_PROMPT,
            tools=[{"function_declarations": GEMINI_TOOLS}],
        )

    def _get_route(self, task: Task, step_index: int = 0):
        """Возвращает RouteDecision для текущего шага."""
        if self.model_router is None:
            from agent.model_router import RouteDecision
            return RouteDecision("gemini", False, "no router → gemini")
        return self.model_router.route(
            prompt     = task.prompt,
            mode       = task.model_mode,
            step_index = step_index,
        )

    # ── Точка входа ──────────────────────────────────────────────

    async def run(self, task: Task) -> Task:
        """Запускаем задачу. Возвращает Task с результатом."""
        task.status = TaskStatus.RUNNING
        logger.info("Agent: задача %s начата: %s", task.id, task.prompt[:80])

        # Открываем запись в журнале
        if self.journal:
            from db.journal import TaskRun, RunStatus
            run = TaskRun(
                id=task.journal_run_id,
                task_id=task.id,
                prompt=task.prompt,
                mode=task.mode,
                status=RunStatus.RUNNING,
            )
            await self.journal.start_run(run)

        # ── Определяем провайдер для этой задачи ──────────────────
        initial_route = self._get_route(task, step_index=0)
        logger.info("Agent: провайдер → %s", initial_route)

        # CLAUDE_ONLY — Claude отвечает сразу без FC-loop
        if initial_route.provider == "claude" and self.claude_adapter:
            return await self._run_claude_only(task, initial_route.use_thinking)

        # CLAUDE_THINK_GEMINI — Claude планирует, Gemini исполняет
        if (
            initial_route.provider == "claude"
            and self.claude_adapter
            and task.model_mode in ("CLAUDE_THINK_GEMINI", "AUTO")
        ):
            enriched_prompt = await self._claude_plan(task, initial_route.use_thinking)
            task.prompt = enriched_prompt

        # Gemini FC-loop (GEMINI_ONLY, GEMINI_THINK_CLAUDE, AUTO с gemini)
        return await self._run_gemini_loop(task)

    # ── Claude ONLY ───────────────────────────────────────────────

    async def _run_claude_only(self, task: Task, use_thinking: bool) -> Task:
        """Задача выполняется полностью через Claude."""
        if not self.claude_adapter:
            task.status = TaskStatus.FAILED
            task.error  = "claude_adapter не настроен"
            await self._close_journal(task)
            return task

        logger.info("Agent: CLAUDE_ONLY режим%s", " +thinking" if use_thinking else "")
        try:
            result = await self.claude_adapter.call_tool(
                task_description = task.prompt,
                use_thinking     = use_thinking,
            )
            task.status      = TaskStatus.DONE
            task.result      = result
            task.finished_at = time.time()
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error  = f"Claude ошибка: {e}"
            logger.error("Agent CLAUDE_ONLY: %s", task.error)

        await self._close_journal(task)
        return task

    # ── Claude планирование ────────────────────────────────────────

    async def _claude_plan(self, task: Task, use_thinking: bool) -> str:
        """
        Вызывает Claude для составления плана выполнения.
        Возвращает enriched prompt для Gemini.
        """
        if not self.claude_adapter:
            return task.prompt

        logger.info("Agent: Claude планирует задачу%s", " +thinking" if use_thinking else "")
        plan_prompt = (
            f"Составь пошаговый план выполнения следующей DevOps-задачи.\n"
            f"Задача: {task.prompt}\n\n"
            f"Укажи конкретные шаги, файлы и команды. Ответ будет передан в Gemini для исполнения."
        )
        try:
            plan = await self.claude_adapter.call_tool(plan_prompt, use_thinking=use_thinking)
            enriched = f"{task.prompt}\n\n[ПЛАН ОТ CLAUDE]\n{plan}"
            logger.info("Agent: план Claude получен (%d chars)", len(plan))
            return enriched
        except Exception as e:
            logger.warning("Agent: Claude планирование упало: %s — продолжаем без плана", e)
            return task.prompt

    # ── Gemini FC-loop ─────────────────────────────────────────────

    async def _run_gemini_loop(self, task: Task) -> Task:
        """Основной Gemini function calling loop."""
        messages = [{"role": "user", "parts": [task.prompt]}]

        for iteration in range(self.max_iterations):
            key   = await self.key_pool.get_key()
            t_llm = time.time()

            try:
                model    = self._build_model(key)
                chat     = model.start_chat(history=messages[:-1])
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: chat.send_message(messages[-1]["parts"])
                )
                latency_ms = int((time.time() - t_llm) * 1000)
                self.key_pool.mark_ok(key)

                if self.journal:
                    from db.journal import LLMCall
                    await self.journal.log_llm_call(LLMCall(
                        run_id=task.journal_run_id,
                        step_id="",
                        key_hint=f"...{key[-6:]}",
                        latency_ms=latency_ms,
                        http_status=200,
                    ))

            except Exception as e:
                err_str    = str(e)
                latency_ms = int((time.time() - t_llm) * 1000)

                if "429" in err_str or "quota" in err_str.lower():
                    self.key_pool.mark_rate_limited(key)
                    logger.warning("Agent: 429 на ключ, пробуем следующий")
                    if self.journal:
                        from db.journal import LLMCall
                        await self.journal.log_llm_call(LLMCall(
                            run_id=task.journal_run_id, step_id="",
                            key_hint=f"...{key[-6:]}", latency_ms=latency_ms,
                            http_status=429, error=err_str[:200],
                        ))
                    continue

                task.status = TaskStatus.FAILED
                task.error  = f"Gemini ошибка: {err_str}"
                logger.error("Agent: %s", task.error)
                await self._close_journal(task)
                return task

            # ── Разбираем ответ ──────────────────────────────────
            candidate = response.candidates[0]
            parts     = candidate.content.parts

            has_tool_call = False
            tool_results  = []

            for part in parts:
                if hasattr(part, "text") and part.text:
                    logger.info("Agent [%d]: %s", iteration, part.text[:120])
                    if len(parts) == 1:
                        task.status      = TaskStatus.DONE
                        task.result      = part.text
                        task.finished_at = time.time()
                        await self._close_journal(task)
                        return task

                if hasattr(part, "function_call") and part.function_call:
                    has_tool_call = True
                    fc          = part.function_call
                    tool_name   = fc.name
                    tool_args   = dict(fc.args) if fc.args else {}

                    logger.info(
                        "Agent [%d]: %s(%s)",
                        iteration, tool_name, str(tool_args)[:80],
                    )

                    envelope = await self._execute_tool(
                        task=task,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        iteration=iteration,
                    )
                    tool_results.append((tool_name, envelope.data))

            if has_tool_call and tool_results:
                messages.append({"role": "model", "parts": parts})
                function_responses = [
                    {
                        "function_response": {
                            "name": name,
                            "response": {
                                "result": json.dumps(result, ensure_ascii=False)
                            },
                        }
                    }
                    for name, result in tool_results
                ]
                messages.append({"role": "user", "parts": function_responses})

                if self.journal and iteration % 5 == 0:
                    await self.journal.save_snapshot(
                        task.journal_run_id,
                        {"iteration": iteration, "steps": len(task.steps), "mode": task.mode},
                    )

        task.status      = TaskStatus.FAILED
        task.error       = f"Превышен лимит итераций ({self.max_iterations})"
        task.finished_at = time.time()
        await self._close_journal(task)
        return task

    # ── Выполнение инструмента ───────────────────────────────────

    async def _execute_tool(
        self,
        task:      Task,
        tool_name: str,
        tool_args: dict,
        iteration: int,
    ) -> "ResultEnvelope":
        invocation_id = str(uuid.uuid4())

        idempotency_key = ""
        if self.registry and tool_name in MUTABLE_TOOLS:
            idempotency_key = self.registry.make_key(task.id, tool_name, tool_args)
            cached = await self.registry.get_cached(idempotency_key)
            if cached:
                envelope = ResultEnvelope.duplicate(cached, invocation_id)
                logger.info(
                    "Agent: %s — дубль, возвращаем кэш [%s]",
                    tool_name, idempotency_key[:8],
                )
                await self._record_step(
                    task=task, tool_name=tool_name, tool_args=tool_args,
                    invocation_id=invocation_id, idempotency_key=idempotency_key,
                    envelope=envelope, duration_ms=0, cached=True,
                )
                return envelope

        if tool_name == "bash_tool" and is_dangerous(tool_args.get("cmd", "")):
            if task.mode != "FULL":
                if self.on_approval_needed:
                    approved = await self.on_approval_needed(task, tool_args.get("cmd", ""))
                    if not approved:
                        envelope = ResultEnvelope.permission_denied(
                            tool_args.get("cmd", ""), invocation_id
                        )
                        await self._record_step(
                            task, tool_name, tool_args, invocation_id,
                            idempotency_key, envelope, 0,
                        )
                        return envelope
                else:
                    envelope = ResultEnvelope.permission_denied(
                        tool_args.get("cmd", ""), invocation_id
                    )
                    await self._record_step(
                        task, tool_name, tool_args, invocation_id,
                        idempotency_key, envelope, 0,
                    )
                    return envelope

        # ── Специальный инструмент: claude_think ────────────────
        if tool_name == "claude_think" and self.claude_adapter:
            return await self._execute_claude_think(
                task, tool_args, invocation_id, idempotency_key
            )

        step    = TaskStep(
            idx=len(task.steps), tool=tool_name, args=tool_args,
            invocation_id=invocation_id, idempotency_key=idempotency_key,
            provider="gemini",
        )
        t0      = time.time()
        tool_fn = TOOLS_REGISTRY.get(tool_name)

        if tool_fn:
            try:
                raw_result = await tool_fn(**tool_args)
            except Exception as e:
                raw_result = {"error": str(e), "ok": False}
        else:
            raw_result = {"error": f"Инструмент '{tool_name}' не найден", "ok": False}

        duration_ms = int((time.time() - t0) * 1000)
        envelope    = ResultEnvelope.from_tool_result(raw_result, invocation_id)

        step.result   = raw_result
        step.duration = duration_ms / 1000
        task.steps.append(step)

        if self.registry and idempotency_key and envelope.ok:
            await self.registry.register(
                idempotency_key=idempotency_key,
                invocation_id=invocation_id,
                task_id=task.id,
                tool_name=tool_name,
                args=tool_args,
                result=raw_result,
            )

        await self._record_step(
            task, tool_name, tool_args, invocation_id,
            idempotency_key, envelope, duration_ms,
        )

        if self.on_step:
            asyncio.create_task(self.on_step(task, step))

        return envelope

    async def _execute_claude_think(
        self,
        task:            Task,
        tool_args:       dict,
        invocation_id:   str,
        idempotency_key: str,
    ) -> "ResultEnvelope":
        """Выполняет claude_think инструмент через ClaudeAdapter."""
        prompt      = tool_args.get("prompt", "")
        context     = tool_args.get("context", "")
        use_thinking = tool_args.get("use_thinking", False)

        t0 = time.time()
        try:
            result_text = await self.claude_adapter.call_tool(
                task_description = prompt,
                context          = context,
                use_thinking     = bool(use_thinking),
            )
            raw_result = {"ok": True, "result": result_text, "provider": "claude"}
        except Exception as e:
            raw_result = {"ok": False, "error": str(e), "provider": "claude"}

        duration_ms = int((time.time() - t0) * 1000)
        envelope    = ResultEnvelope.from_tool_result(raw_result, invocation_id)

        step = TaskStep(
            idx=len(task.steps), tool="claude_think", args=tool_args,
            invocation_id=invocation_id, idempotency_key=idempotency_key,
            result=raw_result, duration=duration_ms / 1000,
            provider="claude",
        )
        task.steps.append(step)

        await self._record_step(
            task, "claude_think", tool_args, invocation_id,
            idempotency_key, envelope, duration_ms,
        )

        if self.on_step:
            asyncio.create_task(self.on_step(task, step))

        return envelope

    # ── Вспомогательные ─────────────────────────────────────────

    async def _record_step(
        self,
        task:            Task,
        tool_name:       str,
        tool_args:       dict,
        invocation_id:   str,
        idempotency_key: str,
        envelope:        "ResultEnvelope",
        duration_ms:     int,
        cached:          bool = False,
    ) -> None:
        if not self.journal:
            return
        from db.journal import StepRecord, RunStatus
        step_id = str(uuid.uuid4())
        step = StepRecord(
            id=step_id,
            run_id=task.journal_run_id,
            step_idx=len(task.steps),
            tool_name=tool_name,
            args=tool_args,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            status=RunStatus.DONE if envelope.ok else RunStatus.FAILED,
            result_json=envelope.data,
            error_code=envelope.error_code.value if envelope.error_code else None,
            retryable=envelope.retryable,
            duration_ms=duration_ms,
            cached=cached,
            ended_at=time.time(),
        )
        await self.journal.start_step(step)
        await self.journal.finish_step(step)

    async def _close_journal(self, task: Task) -> None:
        if not self.journal:
            return
        from db.journal import RunStatus
        status = {
            TaskStatus.DONE:      RunStatus.DONE,
            TaskStatus.FAILED:    RunStatus.FAILED,
            TaskStatus.CANCELLED: RunStatus.SKIPPED,
        }.get(task.status, RunStatus.FAILED)
        await self.journal.finish_run(task.journal_run_id, status)
