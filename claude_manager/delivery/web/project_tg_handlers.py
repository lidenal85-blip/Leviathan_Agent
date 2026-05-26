"""TG-команды для управления проектами Level 6.

Подключается к существующему Router через setup_project_handlers().

Koманды:
  /project <цель>  — запустить новый проект
  /pstatus <id>   — статус проекта
  /ppause <id>    — пауза
  /presume <id>   — возобновить
  /projects       — список проектов
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from claude_manager.core.storage.project_store import ProjectStore, ProjectStatus
from claude_manager.domain.projects.project_executor import ProjectExecutor

if TYPE_CHECKING:
    from claude_manager.providers.pool import LLMProviderPool

logger = logging.getLogger(__name__)

STATUS_EMOJI = {
    ProjectStatus.PLANNING:  "📝",
    ProjectStatus.EXECUTING: "⏳",
    ProjectStatus.WAITING:   "⏰",
    ProjectStatus.PAUSED:    "⏸",
    ProjectStatus.DONE:      "✅",
    ProjectStatus.FAILED:    "🚨",
}


def setup_project_handlers(
    router: Router,
    pool: "LLMProviderPool",
    store: ProjectStore,
    executor: ProjectExecutor,
    admin_chat_id: int,
) -> None:
    """Register all /p* command handlers."""

    def _is_admin(msg: Message) -> bool:
        return msg.chat.id == admin_chat_id

    def _tg_notify(project_id: str, text: str) -> None:
        """Sync callback for executor — schedules coroutine via bot."""
        import asyncio
        try:
            bot = router  # доступ к боту передаётся через _bot_ref
        except Exception:
            pass

    # ── /project <цель> ──────────────────────────────────────

    @router.message(Command("project"))
    async def cmd_project(msg: Message) -> None:
        if not _is_admin(msg):
            return
        goal = (msg.text or "").removeprefix("/project").strip()
        if not goal:
            await msg.answer("⚠️ Укажите цель: /project <описание>")
            return
        session_id = f"tg_{msg.chat.id}"
        await msg.answer(f"📝 Декомпозиция: {goal[:100]}...")
        try:
            pid = await executor.start_project(goal, session_id)
            await msg.answer(
                f"🚀 Проект запущен!\n"
                f"🏷 ID: <code>{pid}</code>\n"
                f"📊 Статус: /pstatus {pid}",
                parse_mode="HTML",
            )
        except Exception as e:
            await msg.answer(f"🚨 Ошибка запуска: {e}")

    # ── /pstatus <id> ─────────────────────────────────────

    @router.message(Command("pstatus"))
    async def cmd_pstatus(msg: Message) -> None:
        if not _is_admin(msg):
            return
        pid = (msg.text or "").removeprefix("/pstatus").strip().split()[0] if msg.text else ""
        if not pid:
            await msg.answer("⚠️ Укажите ID: /pstatus <id>")
            return
        project = await store.get_project(pid)
        if not project:
            await msg.answer(f"❌ Проект `{pid}` не найден")
            return
        emoji = STATUS_EMOJI.get(project.status, "")
        done_steps = sum(1 for s in project.steps if s.status.value == "done")
        lines = [
            f"{emoji} <b>Проект</b> <code>{pid}</code>",
            f"🎯 {project.goal[:120]}",
            f"📊 Шаг: {done_steps}/{project.total_steps} | {project.status.value}",
            "",
        ]
        for s in project.steps:
            em = "✅" if s.status.value == "done" else ("⏳" if s.status.value == "running" else ("🚨" if s.status.value == "failed" else "○"))
            lines.append(f"{em} {s.step_index+1}. {s.description[:80]}")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    # ── /ppause <id> ──────────────────────────────────────

    @router.message(Command("ppause"))
    async def cmd_ppause(msg: Message) -> None:
        if not _is_admin(msg):
            return
        pid = (msg.text or "").removeprefix("/ppause").strip().split()[0] if msg.text else ""
        if not pid:
            await msg.answer("⚠️ Укажите ID: /ppause <id>")
            return
        ok = await executor.pause_project(pid)
        await msg.answer(f"⏸ Проект `{pid}` поставлен на паузу" if ok else f"❌ Не найден: {pid}")

    # ── /presume <id> ─────────────────────────────────────

    @router.message(Command("presume"))
    async def cmd_presume(msg: Message) -> None:
        if not _is_admin(msg):
            return
        pid = (msg.text or "").removeprefix("/presume").strip().split()[0] if msg.text else ""
        if not pid:
            await msg.answer("⚠️ Укажите ID: /presume <id>")
            return
        ok = await executor.resume_project(pid)
        await msg.answer(f"▶️ Проект `{pid}` возобновлён" if ok else f"❌ Нельзя возобновить: {pid}")

    # ── /projects ──────────────────────────────────────────

    @router.message(Command("projects"))
    async def cmd_projects(msg: Message) -> None:
        if not _is_admin(msg):
            return
        projects = await store.list_projects(limit=10)
        if not projects:
            await msg.answer("📂 Проектов нет")
            return
        lines = ["<b>📂 Проекты:</b>"]
        for p in projects:
            emoji = STATUS_EMOJI.get(p.status, "")
            lines.append(
                f"{emoji} <code>{p.project_id}</code> — {p.goal[:60]} "
                f"[{p.current_step}/{p.total_steps}]"
            )
        await msg.answer("\n".join(lines), parse_mode="HTML")