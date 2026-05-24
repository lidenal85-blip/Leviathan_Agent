"""
agent/tg_bot.py — Telegram интерфейс LEVIATHAN AGENT
Команды: /task, /status, /stop, /log, /approve, /deny
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

if TYPE_CHECKING:
    from agent.core import LeviathanAgent, Task, TaskStep
    from db.storage import TaskStorage

logger = logging.getLogger(__name__)
router = Router()


class TelegramNotifier:
    """Отправляет уведомления в Telegram."""

    def __init__(self, bot: Bot, admin_chat_id: int) -> None:
        self.bot = bot
        self.chat_id = admin_chat_id

    async def send(self, text: str, parse_mode: str = "HTML") -> None:
        try:
            await self.bot.send_message(self.chat_id, text, parse_mode=parse_mode)
        except Exception as e:
            logger.error("TG send error: %s", e)

    async def on_task_start(self, task: "Task") -> None:
        await self.send(
            f"🚀 <b>Задача #{task.id} запущена</b>\n"
            f"<code>{task.prompt[:200]}</code>"
        )

    async def on_step(self, task: "Task", step: "TaskStep") -> None:
        icon = "✅" if step.result and step.result.get("ok") else "❌"
        args_preview = str(step.args)[:80]
        await self.send(
            f"{icon} <b>[{task.id}] {step.tool}</b>\n"
            f"<code>{args_preview}</code>\n"
            f"⏱ {step.duration:.1f}s"
        )

    async def on_task_done(self, task: "Task") -> None:
        duration = task.finished_at - task.created_at
        await self.send(
            f"✅ <b>Задача #{task.id} завершена</b> ({duration:.0f}s)\n\n"
            f"{task.result[:1000]}"
        )

    async def on_task_failed(self, task: "Task") -> None:
        await self.send(
            f"❌ <b>Задача #{task.id} провалилась</b>\n"
            f"<code>{task.error[:500]}</code>"
        )

    async def ask_approval(self, task: "Task", cmd: str) -> bool:
        """Запрашиваем подтверждение опасной операции."""
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve:{task.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deny:{task.id}"),
        ]])
        await self.bot.send_message(
            self.chat_id,
            f"⚠️ <b>Опасная операция в задаче #{task.id}</b>\n\n"
            f"<code>{cmd[:300]}</code>\n\n"
            f"Разрешить?",
            parse_mode="HTML",
            reply_markup=kb,
        )
        # Ждём ответа до 60 секунд
        task.pending_approval = {"event": asyncio.Event(), "approved": False}
        try:
            await asyncio.wait_for(task.pending_approval["event"].wait(), timeout=60)
            return task.pending_approval["approved"]
        except asyncio.TimeoutError:
            await self.send(f"⏰ Таймаут подтверждения для задачи #{task.id} — отклонено")
            return False


def setup_bot_handlers(
    router: Router,
    agent_runner: "AgentRunner",
    notifier: "TelegramNotifier",
) -> None:
    """Регистрируем хендлеры бота."""

    @router.message(Command("start"))
    async def cmd_start(msg: Message) -> None:
        await msg.answer(
            "👋 <b>LEVIATHAN AGENT</b>\n\n"
            "Команды:\n"
            "/task <задача> — поставить задачу\n"
            "/status — статус текущей задачи\n"
            "/tasks — последние задачи\n"
            "/stop — остановить агента\n"
            "/log — последние шаги\n",
            parse_mode="HTML",
        )

    @router.message(Command("task"))
    async def cmd_task(msg: Message) -> None:
        prompt = msg.text.removeprefix("/task").strip()
        if not prompt:
            await msg.answer("❗ Укажи задачу: /task <описание>")
            return

        task = await agent_runner.submit(prompt)
        await msg.answer(
            f"✅ Задача <b>#{task.id}</b> принята\n"
            f"<code>{prompt[:100]}</code>",
            parse_mode="HTML",
        )

    @router.message(Command("status"))
    async def cmd_status(msg: Message) -> None:
        task = agent_runner.current_task
        if not task:
            await msg.answer("💤 Нет активных задач")
            return
        steps_count = len(task.steps)
        await msg.answer(
            f"📋 <b>Задача #{task.id}</b>\n"
            f"Статус: <b>{task.status.value}</b>\n"
            f"Шагов выполнено: {steps_count}\n"
            f"<code>{task.prompt[:100]}</code>",
            parse_mode="HTML",
        )

    @router.message(Command("tasks"))
    async def cmd_tasks(msg: Message) -> None:
        tasks = await agent_runner.storage.list_recent(10)
        if not tasks:
            await msg.answer("📭 История задач пуста")
            return
        lines = []
        for t in tasks:
            icon = {"done": "✅", "failed": "❌", "running": "⟳", "pending": "⏳"}.get(t.status.value, "•")
            lines.append(f"{icon} <b>#{t.id}</b> {t.prompt[:50]}")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("stop"))
    async def cmd_stop(msg: Message) -> None:
        if agent_runner.current_task:
            agent_runner.cancel_current()
            await msg.answer("🛑 Задача остановлена")
        else:
            await msg.answer("💤 Нет активных задач")

    @router.message(Command("log"))
    async def cmd_log(msg: Message) -> None:
        task = agent_runner.current_task
        if not task or not task.steps:
            await msg.answer("📭 Нет шагов")
            return
        lines = []
        for step in task.steps[-10:]:
            ok = "✅" if step.result and step.result.get("ok") else "❌"
            lines.append(f"{ok} {step.tool}({str(step.args)[:40]}) [{step.duration:.1f}s]")
        await msg.answer("\n".join(lines))

    @router.callback_query(F.data.startswith("approve:"))
    async def cb_approve(cb: CallbackQuery) -> None:
        task_id = cb.data.split(":")[1]
        task = agent_runner.current_task
        if task and task.id == task_id and task.pending_approval:
            task.pending_approval["approved"] = True
            task.pending_approval["event"].set()
        await cb.answer("✅ Разрешено")

    @router.callback_query(F.data.startswith("deny:"))
    async def cb_deny(cb: CallbackQuery) -> None:
        task_id = cb.data.split(":")[1]
        task = agent_runner.current_task
        if task and task.id == task_id and task.pending_approval:
            task.pending_approval["approved"] = False
            task.pending_approval["event"].set()
        await cb.answer("❌ Отклонено")


class AgentRunner:
    """Управляет очередью задач."""

    def __init__(
        self,
        agent: "LeviathanAgent",
        storage: "TaskStorage",
        notifier: "TelegramNotifier",
    ) -> None:
        self.agent = agent
        self.storage = storage
        self.notifier = notifier
        self.current_task: "Task | None" = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event = asyncio.Event()
        self._ws_clients: set = set()  # WebSocket клиенты

    async def submit(self, prompt: str, mode: str = "NORMAL") -> "Task":
        from agent.core import Task
        task = Task(prompt=prompt, mode=mode)
        await self.storage.save(task)
        await self._queue.put(task)
        return task

    def cancel_current(self) -> None:
        self._cancel_event.set()

    async def run_loop(self) -> None:
        """Основной цикл обработки задач."""
        logger.info("AgentRunner: запущен")
        while True:
            task = await self._queue.get()
            self.current_task = task
            self._cancel_event.clear()

            await self.notifier.on_task_start(task)

            # Устанавливаем callbacks
            self.agent.on_step = self._on_step
            self.agent.on_approval_needed = self.notifier.ask_approval

            try:
                completed = await self.agent.run(task)
                await self.storage.save(completed)

                if completed.status.value == "done":
                    await self.notifier.on_task_done(completed)
                else:
                    await self.notifier.on_task_failed(completed)

            except asyncio.CancelledError:
                from agent.core import TaskStatus
                task.status = TaskStatus.CANCELLED
                await self.storage.save(task)
                await self.notifier.send(f"🛑 Задача #{task.id} отменена")
            except Exception as e:
                logger.error("AgentRunner: ошибка: %s", e)
                await self.notifier.send(f"💥 Внутренняя ошибка: {e}")
            finally:
                self.current_task = None

    async def _on_step(self, task: "Task", step: "TaskStep") -> None:
        """Уведомление о шаге — в TG и WebSocket."""
        await self.notifier.on_step(task, step)
        await self.storage.save(task)
        # Рассылаем WS клиентам
        for ws in list(self._ws_clients):
            try:
                import json
                await ws.send_text(json.dumps({
                    "type": "step",
                    "task_id": task.id,
                    "tool": step.tool,
                    "ok": step.result.get("ok", False) if step.result else False,
                    "duration": step.duration,
                }))
            except Exception:
                self._ws_clients.discard(ws)
