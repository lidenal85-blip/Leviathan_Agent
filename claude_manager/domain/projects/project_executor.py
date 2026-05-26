"""ProjectExecutor — запуск, выполнение и возобновление проектов."""
from __future__ import annotations

import asyncio
from typing import Callable, Optional, TYPE_CHECKING

from claude_manager.core.storage.project_store import (
    ProjectStore, ProjectStatus, StepStatus,
)
from claude_manager.domain.projects.task_planner import TaskPlanner
from claude_manager.logger import StepLogger

if TYPE_CHECKING:
    from claude_manager.providers.pool import LLMProviderPool

_log = StepLogger("project_executor")

STEP_PROMPT = """\
Ты выполняешь проект: {goal}

Уже выполненные шаги:
{previous}

Текущий шаг ({step_num}/{total}):
{description}

Выполни этот шаг. Дай конкретный результат (код, инструкции, текст).
"""


class ProjectExecutor:
    def __init__(
        self,
        pool: "LLMProviderPool",
        store: ProjectStore,
        notify: Optional[Callable[[str, str], None]] = None,
    ):
        """
        pool:   LLMProviderPool
        store:  ProjectStore
        notify: callback(project_id, message) — для отправки в TG
        """
        self._pool    = pool
        self._store   = store
        self._planner = TaskPlanner(pool)
        self._notify  = notify
        self._running: set[str] = set()   # активные проекты

    # ── Публичный API ──────────────────────────────────────────

    async def start_project(self, goal: str, session_id: str = "") -> str:
        """Creates project, decomposes goal, starts execution in background."""
        _log.task(f"запуск проекта: {goal[:80]}")
        project_id = await self._store.create_project(goal, session_id)

        _log.step("декомпозиция через TaskPlanner")
        steps = await self._planner.decompose(goal, session_id)
        await self._store.save_steps(project_id, steps)

        self._notify_safe(
            project_id,
            f"🚀 Проект `{project_id}` запущен\n"
            f"🎯 {goal[:100]}\n"
            f"📌 Шагов: {len(steps)}",
        )

        # запуск в фоне
        asyncio.create_task(self._run_project(project_id))
        _log.result(f"проект {project_id} запущен в фоне, {len(steps)} шагов")
        return project_id

    async def pause_project(self, project_id: str) -> bool:
        _log.task(f"пауза проекта {project_id}")
        self._running.discard(project_id)
        await self._store.set_status(project_id, ProjectStatus.PAUSED)
        _log.result(f"проект {project_id} поставлен на паузу")
        self._notify_safe(project_id, f"⏸ Проект `{project_id}` поставлен на паузу")
        return True

    async def resume_project(self, project_id: str) -> bool:
        _log.task(f"возобновление проекта {project_id}")
        project = await self._store.get_project(project_id)
        if not project:
            _log.error(f"проект {project_id} не найден")
            return False
        if project.status not in (ProjectStatus.PAUSED, ProjectStatus.WAITING, ProjectStatus.FAILED):
            _log.warn(f"проект {project_id} в статусе {project.status}, нельзя возобновить")
            return False
        await self._store.set_status(project_id, ProjectStatus.EXECUTING)
        self._notify_safe(project_id, f"▶️ Проект `{project_id}` возобновлён")
        asyncio.create_task(self._run_project(project_id))
        _log.result(f"проект {project_id} возобновлён")
        return True

    # ── Внутреннее выполнение ─────────────────────────────────

    async def _run_project(self, project_id: str) -> None:
        self._running.add(project_id)
        try:
            await self._execute_loop(project_id)
        except Exception as e:
            _log.error(f"непредвиденная ошибка проекта {project_id}: {e}")
            await self._store.set_status(project_id, ProjectStatus.FAILED)
            self._notify_safe(project_id, f"🚨 Проект `{project_id}` упал: {e}")
        finally:
            self._running.discard(project_id)

    async def _execute_loop(self, project_id: str) -> None:
        while project_id in self._running:
            project = await self._store.get_project(project_id)
            if not project:
                break

            if project.status == ProjectStatus.PAUSED:
                _log.step(f"{project_id}: пауза, выходим")
                break

            if project.current_step >= project.total_steps:
                await self._store.set_status(project_id, ProjectStatus.DONE)
                _log.result(f"проект {project_id} завершён")
                self._notify_safe(
                    project_id,
                    f"✅ Проект `{project_id}` выполнен!\n🎯 {project.goal[:100]}",
                )
                break

            step = project.steps[project.current_step]
            await self._execute_step(project, step)

    async def _execute_step(self, project, step) -> None:
        pid    = project.project_id
        si     = step.step_index
        _log.task(f"шаг {si+1}/{project.total_steps}: {step.description[:60]}")
        await self._store.update_step(pid, si, StepStatus.RUNNING)

        self._notify_safe(
            pid,
            f"⏳ Шаг {si+1}/{project.total_steps}\n{step.description[:120]}",
        )

        previous = self._format_previous(project.steps[:si])
        prompt = STEP_PROMPT.format(
            goal=project.goal,
            previous=previous or "нет",
            step_num=si + 1,
            total=project.total_steps,
            description=step.description,
        )

        try:
            result = await self._pool.complete(
                prompt=prompt,
                session_id=project.session_id or pid,
                system="Ты автономный исполнитель. Давай чёткий код / действия.",
            )
            await self._store.update_step(pid, si, StepStatus.DONE, result=result[:2000])
            await self._store.advance(pid, ProjectStatus.EXECUTING, si + 1)
            _log.result(f"шаг {si+1} выполнен")
            self._notify_safe(
                pid,
                f"✅ Шаг {si+1} завершён\n{result[:300]}",
            )

        except Exception as e:
            err = str(e)
            _log.error(f"шаг {si+1} упал: {err}")
            await self._store.update_step(pid, si, StepStatus.FAILED, error=err)
            await self._store.set_status(pid, ProjectStatus.FAILED)
            self._notify_safe(pid, f"🚨 Шаг {si+1} упал: {err[:200]}")
            self._running.discard(pid)

    @staticmethod
    def _format_previous(steps) -> str:
        lines = []
        for s in steps:
            if s.result:
                lines.append(f"Шаг {s.step_index+1}: {s.description}\nРезультат: {s.result[:300]}")
        return "\n\n".join(lines[-5:])  # последние 5 шагов в контекст

    def _notify_safe(self, project_id: str, message: str) -> None:
        if self._notify:
            try:
                self._notify(project_id, message)
            except Exception:
                pass