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
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)

if TYPE_CHECKING:
    from agent.core import LeviathanAgent, Task, TaskStep
    from db.storage import TaskStorage

logger = logging.getLogger(__name__)
router = Router()


def _main_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⚡ Статус"),
                KeyboardButton(text="📋 Задачи"),
                KeyboardButton(text="📊 Метрики"),
            ],
            [
                KeyboardButton(text="🧠 Модель"),
                KeyboardButton(text="🔑 Ключи"),
                KeyboardButton(text="🛑 Стоп"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def _model_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-кнопки выбора модели."""
    modes = [
        ("🧠 AUTO",              "model:AUTO"),
        ("⚡ Gemini",           "model:GEMINI_ONLY"),
        ("🟣 Groq",             "model:GROQ_ONLY"),
        ("🟦 Claude",           "model:CLAUDE_ONLY"),
        ("⚡🟦 Gemini+Claude",  "model:GEMINI_THINK_CLAUDE"),
        ("⚡🟣 Gemini+Groq",    "model:GEMINI_GROQ"),
        ("🟦⚡ Claude+Gemini",  "model:CLAUDE_THINK_GEMINI"),
        ("🟦🟣 Claude+Groq",    "model:CLAUDE_GROQ"),
        ("🌀 FULL",             "model:FULL"),
    ]
    rows = []
    for i in range(0, len(modes), 3):
        rows.append([InlineKeyboardButton(text=t, callback_data=d) for t, d in modes[i:i+3]])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class TelegramNotifier:
    """Отправляет уведомления в Telegram."""

    def __init__(self, bot: Bot, admin_chat_id: int) -> None:
        self.bot = bot
        self.chat_id = admin_chat_id

    async def send(self, text: str, parse_mode: str = "HTML", reply_markup=None) -> None:
        TG_LIMIT = 4000
        chunks = [text[i:i+TG_LIMIT] for i in range(0, max(len(text), 1), TG_LIMIT)]
        for i, chunk in enumerate(chunks):
            try:
                kb = reply_markup if i == len(chunks) - 1 else None
                await self.bot.send_message(self.chat_id, chunk, parse_mode=parse_mode, reply_markup=kb)
            except Exception as e:
                logger.error("TG send error: %s", e); break

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
        header = f"✅ <b>Задача #{task.id} завершена</b> ({duration:.0f}s)\n\n"
        await self.send(header + (task.result or ""), reply_markup=_main_keyboard())

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
            "Кнопки внизу — быстрый доступ. Или пиши задачу свободным текстом.\n\n"
            "Команды: /task /status /model /stop /log",
            parse_mode="HTML",
            reply_markup=_main_keyboard(),
        )

    @router.message(F.text == "⚡ Статус")
    async def btn_status(msg: Message) -> None:
        task = agent_runner.current_task
        q = agent_runner._queue.qsize()
        if not task:
            txt = f"💤 Агент свободен"
            if q: txt += f" | в очереди: {q}"
            await msg.answer(txt)
            return
        steps = task.steps
        last = steps[-1] if steps else None
        last_txt = f"\n🔧 {last.tool} ({last.duration:.1f}s)" if last else ""
        icons = {"running":"🔄","pending":"⏳","done":"✅","failed":"❌","paused":"⏸️"}
        icon = icons.get(task.status.value, "•")
        from agent.model_router import get_router
        mode = get_router().default_mode.value
        await msg.answer(
            f"{icon} <b>#{task.id[:8]}</b> — {task.status.value.upper()}\n"
            f"🧠 Модель: <code>{mode}</code>\n"
            f"📊 Шагов: {len(steps)}"
            f"{last_txt}\n"
            f"💬 <code>{task.prompt[:80]}</code>",
            parse_mode="HTML"
        )

    @router.message(F.text == "📋 Задачи")
    async def btn_tasks(msg: Message) -> None:
        tasks = await agent_runner.storage.list_recent(8)
        q = agent_runner._queue.qsize()
        if not tasks:
            await msg.answer("📭 История пуста")
            return
        icons = {"done":"✅","failed":"❌","running":"🔄","pending":"⏳","paused":"⏸️"}
        await msg.answer(f"📊 <b>Задачи</b> (очередь: {q})", parse_mode="HTML")

        for t in tasks:
            ic  = icons.get(t.status.value, "•")
            ts  = time.strftime("%H:%M %d.%m", time.localtime(t.created_at))
            dur = f" {t.finished_at - t.created_at:.0f}s" if t.finished_at else ""
            txt = (
                f"{ic} <code>{t.id[:8]}</code> [{ts}]{dur}\n"
                f"{t.prompt[:60]}"
            )
            # Кнопки действия по статусу
            btns = []
            if t.status.value in ("failed", "done"):
                btns.append(InlineKeyboardButton(
                    text="🔁 Повторить", callback_data=f"resume:{t.id}:restart"
                ))
            elif t.status.value == "paused":
                btns.append(InlineKeyboardButton(
                    text="▶️ Продолжить", callback_data=f"resume:{t.id}:hot"
                ))
            btns.append(InlineKeyboardButton(
                text="📋 Подробнее", callback_data=f"taskinfo:{t.id}"
            ))
            kb = InlineKeyboardMarkup(inline_keyboard=[btns]) if btns else None
            await msg.answer(txt, parse_mode="HTML", reply_markup=kb)

    @router.callback_query(F.data.startswith("resume:"))
    async def cb_resume(cb: CallbackQuery) -> None:
        _, task_id, mode = cb.data.split(":")
        task = await agent_runner.storage.get(task_id)
        if not task:
            await cb.answer("❌ Задача не найдена")
            return

        if mode == "hot" and task.status.value == "paused":
            # Возобновление с последнего шага
            from agent.core import TaskStatus
            from execution.pipeline_log import PipelineEvent, plog
            task.status = TaskStatus.RUNNING
            task.paused_at = 0.0
            await agent_runner.storage.save(task)
            plog(task.id, PipelineEvent.TASK_RESUMED, f"шаг={task.current_step} (TG кнопка)")
            await agent_runner._queue.put(task)
            await cb.message.edit_text(
                f"▶️ Задача <code>{task_id[:8]}</code> возобновлена с шага {task.current_step}",
                parse_mode="HTML"
            )
        else:
            # Перезапуск с начала
            new_task = await agent_runner.submit(
                task.prompt, mode=task.mode,
                model_mode=task.model_mode,
                fire_and_forget=task.fire_and_forget,
            )
            await cb.message.edit_text(
                f"🔁 Новая задача <code>{new_task.id[:8]}</code> запущена",
                parse_mode="HTML"
            )
        await cb.answer()

    @router.callback_query(F.data.startswith("taskinfo:"))
    async def cb_taskinfo(cb: CallbackQuery) -> None:
        task_id = cb.data.split(":")[1]
        task = await agent_runner.storage.get(task_id)
        if not task:
            await cb.answer("❌ Не найдена")
            return
        icons = {"done":"✅","failed":"❌","running":"🔄","pending":"⏳","paused":"⏸️"}
        ic = icons.get(task.status.value, "•")
        dur = f"{task.finished_at - task.created_at:.0f}s" if task.finished_at else "—"
        steps_txt = ""
        for s in task.steps[-5:]:
            ok = "✅" if s.get("ok") else "❌"
            steps_txt += f"  {ok} {s.get('tool','?')} ({s.get('duration',0):.1f}s)\n"
        err = f"\n❌ <code>{task.error[:200]}</code>" if task.error else ""
        await cb.message.answer(
            f"{ic} <b>Задача #{task.id[:8]}</b>\n"
            f"Статус: <b>{task.status.value}</b> | Время: {dur}\n"
            f"Шагов: {len(task.steps)}\n\n"
            f"<code>{task.prompt[:150]}</code>\n\n"
            f"{steps_txt}{err}",
            parse_mode="HTML"
        )
        await cb.answer()

    @router.message(F.text == "📊 Метрики")
    async def btn_metrics(msg: Message) -> None:
        import httpx
        from agent.model_router import get_router
        mode = get_router().default_mode.value
        tasks = await agent_runner.storage.list_recent(50)
        done  = sum(1 for t in tasks if t.status.value == "done")
        failed= sum(1 for t in tasks if t.status.value == "failed")
        total_steps = sum(len(t.steps) for t in tasks)
        # Пингуем сервисы экосистемы
        services = [("Agent",8200),("Arbitr",8095),("VoiceStudio",8120),("KinoVibe",8110)]
        svc_lines = []
        async with httpx.AsyncClient(timeout=2) as client:
            for name, port in services:
                try:
                    r = await client.get(f"http://localhost:{port}/health")
                    svc_lines.append(f"  ✅ {name}:{port}")
                except Exception:
                    svc_lines.append(f"  ❌ {name}:{port}")
        from core_bridge.key_pool import GeminiKeyPool
        keys_ok = sum(1 for k in agent_runner.agent.key_pool._keys if k.is_available) if hasattr(agent_runner.agent, 'key_pool') else '?'
        await msg.answer(
            f"📊 <b>Метрики</b>\n\n"
            f"🧠 Модель: <code>{mode}</code>\n"
            f"🔑 Gemini ключей: {keys_ok}/14\n\n"
            f"📌 Задачи (последние 50):\n"
            f"  ✅ Выполнено: {done}\n"
            f"  ❌ Ошибок: {failed}\n"
            f"  🔧 Шагов всего: {total_steps}\n\n"
            f"🌐 Сервисы:\n" + "\n".join(svc_lines),
            parse_mode="HTML"
        )

    @router.message(F.text == "🧠 Модель")
    async def btn_model(msg: Message) -> None:
        from agent.model_router import get_router
        current = get_router().default_mode.value
        await msg.answer(
            f"🧠 <b>Текущая модель:</b> <code>{current}</code>\nВыбери новую:",
            parse_mode="HTML",
            reply_markup=_model_keyboard()
        )

    @router.message(F.text == "🔑 Ключи")
    async def btn_keys(msg: Message) -> None:
        try:
            stats = agent_runner.agent.key_pool.stats()
            lines = []
            for s in stats:
                icon = "✅" if s["available"] else f"🔴 ({s['blocked_for']:.0f}s)"
                lines.append(f"{icon} <code>...{s['key']}</code> — {s['requests']} запр/{s['failures']} ошиб")
            await msg.answer(
                f"🔑 <b>Gemini ключи</b> ({sum(1 for s in stats if s['available'])}/{len(stats)} доступно):\n\n" + "\n".join(lines),
                parse_mode="HTML"
            )
        except Exception as e:
            await msg.answer(f"❌ {e}")

    @router.message(F.text == "🛑 Стоп")
    async def btn_stop(msg: Message) -> None:
        if agent_runner.current_task:
            tid = agent_runner.current_task.id[:8]
            agent_runner.cancel_current()
            await msg.answer(f"🛑 Задача #{tid} остановлена")
        else:
            await msg.answer("💤 Нет активных задач")

    @router.callback_query(F.data.startswith("model:"))
    async def cb_model_select(cb: CallbackQuery) -> None:
        from agent.model_router import ModelMode, get_router
        mode_str = cb.data.split(":", 1)[1]
        try:
            new_mode = ModelMode(mode_str)
            get_router().default_mode = new_mode
            await cb.message.edit_text(
                f"✅ Модель переключена: <code>{new_mode.value}</code>",
                parse_mode="HTML"
            )
        except ValueError:
            await cb.answer(f"❌ Неизвестный режим: {mode_str}")
        await cb.answer()

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

    # ── Claude Multi-Account ────────────────────────────────────────────────────────────

    @router.message(Command("claude_add"))
    async def cmd_claude_add(msg: Message) -> None:
        """/claude_add email session_key [password]

        session_key — из DevTools → Application → Cookies → claude.ai → sessionKey
        password    — опционально, для авто-ротации через Playwright
        """
        parts = (msg.text or "").split(maxsplit=3)
        if len(parts) < 3:
            await msg.answer(
                "❌ Формат: /claude_add email session_key [password]\n\n"
                "Как получить sessionKey:\n"
                "1. Открой claude.ai, войди в аккаунт\n"
                "2. F12 → Application → Cookies → claude.ai\n"
                "3. Найди sessionKey, скопируй"
            )
            return
        email       = parts[1]
        session_key = parts[2]
        password    = parts[3] if len(parts) > 3 else ""
        try:
            from claude_manager.core.storage.account_store import AccountStore as _AS
            import importlib
            cfg = importlib.import_module("config.settings")
            _store = _AS(getattr(cfg, "CLAUDE_ACCOUNTS_DB", "db/claude_accounts.db"))
            await _store.init()
            account_id = await _store.add(email, session_key, password)
            pw_note = " + password сохранён (авто-ротация активна)" if password else ""
            await msg.answer(
                f"✅ Аккаунт добавлен{pw_note}\n"
                f"id: {account_id}\nemail: {email}"
            )
        except Exception as exc:
            await msg.answer(f"❌ Ошибка: {exc}")

    @router.message(Command("claude_key"))
    async def cmd_claude_key(msg: Message) -> None:
        """/claude_key account_id session_key — обновить истёкший sessionKey"""
        parts = (msg.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await msg.answer("❌ Формат: /claude_key account_id session_key")
            return
        account_id, session_key = parts[1], parts[2]
        try:
            from claude_manager.core.storage.account_store import AccountStore as _AS
            import importlib
            cfg = importlib.import_module("config.settings")
            _store = _AS(getattr(cfg, "CLAUDE_ACCOUNTS_DB", "db/claude_accounts.db"))
            await _store.init()
            await _store.update_session_key(account_id, session_key)
            await _store.update_status(account_id, "ACTIVE")
            await msg.answer(f"✅ sessionKey обновлён, статус → ACTIVE\nacc: {account_id}")
        except Exception as exc:
            await msg.answer(f"❌ Ошибка: {exc}")

    @router.message(Command("claude_status"))
    async def cmd_claude_status(msg: Message) -> None:
        """/claude_status — показать все аккаунты Claude"""
        try:
            from claude_manager.core.storage.account_store import AccountStore as _AS
            import importlib
            cfg = importlib.import_module("config.settings")
            _store = _AS(getattr(cfg, "CLAUDE_ACCOUNTS_DB", "db/claude_accounts.db"))
            await _store.init()
            accounts = await _store.list_all()
            if not accounts:
                await msg.answer("💭 Аккаунтов нет. Добавь: /claude_add email session_key")
                return
            icons = {"ACTIVE": "✅", "AUTH_FAILED": "❌", "DEAD": "💣",
                     "DEGRADED": "⚠️", "NEEDS_KEY": "🔑", "RATE_LIMITED": "⏳"}
            lines = ["📄 Claude аккаунты:"]
            for a in accounts:
                st = a.status.value if hasattr(a.status, "value") else str(a.status)
                icon = icons.get(st, "❓")
                lines.append(f"{icon} {a.email[:20]}... | {st} | id:{a.account_id}")
            await msg.answer("\n".join(lines))
        except Exception as exc:
            await msg.answer(f"❌ Ошибка: {exc}")

    @router.message(Command("model"))
    async def cmd_model(msg: Message) -> None:
        """/model [auto|gemini|claude|groq|gemini_think_claude|claude_think_gemini] — переключить LLM.
        Без аргумента — показать текущий режим.
        """
        from agent.model_router import ModelMode, get_router
        parts = (msg.text or "").split(maxsplit=1)

        _router = get_router()
        current = _router.default_mode.value

        if len(parts) == 1:
            modes_desc = (
                "❓ <b>/model</b> — смена LLM\n\n"
                f"✅ <b>Текущий:</b> <code>{current}</code>\n\n"
                "Доступные режимы:\n"
                "• <code>auto</code> — авто по содержимом\n"
                "• <code>gemini</code> — только Gemini (быстро, дешево)\n"
                "• <code>claude</code> — только Claude (архитектура, код)\n"
                "• <code>groq</code> — только Groq\n"
                "• <code>gemini_think_claude</code> — Gemini loop + Claude для сложных шагов\n"
                "• <code>claude_think_gemini</code> — Claude планирует, Gemini исполняет\n"
                "• <code>gemini_groq</code> — Gemini + Groq дешёвый режим\n"
                "• <code>claude_groq</code> — Claude анализ + Groq быстрые операции\n"
                "• <code>full</code> — Claude+Gemini+Groq все три"
            )
            await msg.answer(modes_desc, parse_mode="HTML")
            return

        raw = parts[1].strip().upper()
        alias_map = {
            "AUTO":   "AUTO",
            "GEMINI": "GEMINI_ONLY",
            "CLAUDE": "CLAUDE_ONLY",
            "GROQ":   "GROQ_ONLY",
            "GEMINI_THINK_CLAUDE": "GEMINI_THINK_CLAUDE",
            "GEMINI_GROQ":         "GEMINI_GROQ",
            "CLAUDE_THINK_GEMINI": "CLAUDE_THINK_GEMINI",
            "CLAUDE_GROQ":         "CLAUDE_GROQ",
            "FULL":   "FULL",
        }
        mode_str = alias_map.get(raw, raw)
        try:
            new_mode = ModelMode(mode_str)
            _router.default_mode = new_mode
            icons = {
                "AUTO":               "🧠",
                "GEMINI_ONLY":        "⚡",
                "CLAUDE_ONLY":        "🧠🔵",
                "GROQ_ONLY":          "🟣",
                "GEMINI_THINK_CLAUDE": "⚡🧠",
                "CLAUDE_THINK_GEMINI": "🧠⚡",
            }
            icon = icons.get(mode_str, "🔄")
            await msg.answer(
                f"{icon} Режим переключён: <code>{new_mode.value}</code>\n"
                f"Следующая задача будет выполнена через <code>{new_mode.value}</code>",
                parse_mode="HTML",
            )
        except ValueError:
            await msg.answer(
                f"❌ Неизвестный режим: <code>{raw}</code>\n"
                "Используй: auto|gemini|claude|groq|gemini_groq|gemini_think_claude|claude_think_gemini|claude_groq|full",
                parse_mode="HTML",
            )

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

    # ── Prompt Architect: хранилище планов ожидающих подтверждения ──────────
    _pending_plans: dict[str, object] = {}

    @router.message(F.text & ~F.text.startswith("/"))
    async def free_text_handler(msg: Message) -> None:
        """
        Свободный текст → Prompt Architect анализирует.
        Простая задача  → сразу в работу.
        Сложная задача  → показываем план + кнопки Запустить/Изменить/Отмена.
        """
        if not agent_runner:
            await msg.answer("❌ Агент не инициализирован")
            return

        prompt = msg.text.strip()

        # Кнопки постоянной клавиатуры — обрабатываем как команды, не задачи
        KEYBOARD_BTNS = {"⚡ Статус", "📋 Задачи", "📊 Метрики", "🧠 Модель", "🔑 Ключи", "🛑 Стоп"}
        if prompt in KEYBOARD_BTNS:
            return

        from agent.prompt_architect import PromptArchitect, format_plan_message
        architect: PromptArchitect = getattr(agent_runner, "_architect", None)
        if architect is None:
            architect = PromptArchitect(llm_pool=None)

        # Анализируем
        plan = await architect.analyze(prompt)

        if not plan.is_complex:
            # Простая задача — сразу запускаем
            task = await agent_runner.submit(prompt, mode="NORMAL")
            await msg.answer(
                f"➡️ <code>{task.id[:8]}</code> — выполняю",
                parse_mode="HTML"
            )
            return

        # Сложная задача — показываем план
        import uuid
        plan_id = uuid.uuid4().hex[:12]
        _pending_plans[plan_id] = plan

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="▶️ Запустить",  callback_data=f"plan:run:{plan_id}"),
            InlineKeyboardButton(text="✏️ Изменить",   callback_data=f"plan:edit:{plan_id}"),
            InlineKeyboardButton(text="❌ Отмена",      callback_data=f"plan:cancel:{plan_id}"),
        ]])
        await msg.answer(
            format_plan_message(plan),
            parse_mode="HTML",
            reply_markup=kb,
        )

    @router.callback_query(F.data.startswith("plan:"))
    async def cb_plan(cb: CallbackQuery) -> None:
        parts   = cb.data.split(":")
        action  = parts[1]   # run | edit | cancel
        plan_id = parts[2]

        from agent.prompt_architect import ArchitectPlan
        plan: ArchitectPlan = _pending_plans.get(plan_id)

        if action == "cancel" or plan is None:
            _pending_plans.pop(plan_id, None)
            await cb.message.edit_text("❌ Отменено", reply_markup=None)
            await cb.answer()
            return

        if action == "edit":
            await cb.message.edit_text(
                f"✏️ Отправь уточнённую версию задачи (или /cancel):\n"
                f"<code>{plan.improved_prompt}</code>",
                parse_mode="HTML",
                reply_markup=None,
            )
            # Помечаем план как «ожидает редактирования» — следующее
            # сообщение пользователя станет новой задачей через free_text
            _pending_plans.pop(plan_id, None)
            await cb.answer()
            return

        # action == run
        _pending_plans.pop(plan_id, None)
        final_prompt = plan.improved_prompt
        task = await agent_runner.submit(
            final_prompt,
            mode="NORMAL",
            fire_and_forget=True,
        )
        await cb.message.edit_text(
            f"⏳ Запущено <code>{task.id[:8]}</code>\n"
            f"Уведомлю когда завершится.",
            parse_mode="HTML",
            reply_markup=None,
        )
        await cb.answer("▶️ Запущено")


class AgentRunner:
    """Управляет очередью задач."""

    def __init__(
        self,
        agent: "LeviathanAgent",
        storage: "TaskStorage",
        notifier: "TelegramNotifier",
        kb=None,
    ) -> None:
        self.agent = agent
        self.storage = storage
        self.notifier = notifier
        self.current_task: "Task | None" = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event = asyncio.Event()
        self._ws_clients: set = set()
        self.kb = kb  # KnowledgeBase для сохранения опыта после задач
        # Prompt Architect
        from agent.prompt_architect import PromptArchitect
        self._architect = PromptArchitect(llm_pool=None)  # pool подключается через set_pool()
        # Phase 1: _storage_ref для backoff save
        self.agent._storage_ref = storage

    async def submit(
        self,
        prompt:          str,
        mode:            str = "NORMAL",
        model_mode:      str | None = None,
        fire_and_forget: bool = False,
    ) -> "Task":
        from agent.core import Task
        task = Task(
            prompt          = prompt,
            mode            = mode,
            model_mode      = model_mode,
            fire_and_forget = fire_and_forget,
        )
        await self.storage.save(task)
        await self._queue.put(task)
        return task

    def cancel_current(self) -> None:
        self._cancel_event.set()

    async def run_loop(self) -> None:
        """Основной цикл обработки задач."""
        logger.info("AgentRunner: запущен")
        # Phase 1: hot-resume PAUSED/RUNNING задач при старте
        await self._hot_resume_paused()
        while True:
            task = await self._queue.get()
            self.current_task = task
            self._cancel_event.clear()

            await self.notifier.on_task_start(task)

            # Phase 1: fire_and_forget — не отправляем start-уведомление
            if task.fire_and_forget:
                await self.notifier.send(f"⏳ Задача #{task.id} запущена в фоне")

            # Устанавливаем callbacks
            self.agent.on_step = self._on_step
            self.agent.on_approval_needed = self.notifier.ask_approval

            try:
                completed = await self.agent.run(task)
                await self.storage.save(completed)

                if completed.status.value == "done":
                    # ── KnowledgeBase: сохраняем опыт агента ──
                    if self.kb:
                        asyncio.create_task(self.kb.save_entry(
                            task_id    = completed.id,
                            summary    = (completed.result or "")[:500],
                            tools_used = list({s.tool for s in completed.steps}),
                            outcome    = "done",
                        ))
                    if task.fire_and_forget:
                        # Fire-and-forget: одна финальная строка
                        result_preview = (completed.result or "")[:300]
                        await self.notifier.send(
                            f"Конвейер завершён. Результат: {result_preview}"
                        )
                    else:
                        await self.notifier.on_task_done(completed)
                else:
                    await self.notifier.on_task_failed(completed)

                # ━━ FileLogger ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                try:
                    from db.file_logger import get_file_logger
                    get_file_logger().log_task(completed)
                except Exception as _log_err:
                    logger.warning("FileLogger skipped: %s", _log_err)

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

    async def _hot_resume_paused(self) -> None:
        """Восстанавливаем PAUSED/RUNNING задачи после перезапуска сервиса."""
        from execution.pipeline_log import PipelineEvent, plog
        try:
            paused = await self.storage.get_paused_tasks()
        except Exception as e:
            logger.warning("hot_resume: ошибка при чтении PAUSED: %s", e)
            return
        if not paused:
            return
        logger.info("hot_resume: найдено %d прерванных задач", len(paused))
        for task in paused:
            plog(task.id, PipelineEvent.HOT_RESUME,
                 f"шаг={task.current_step} status_was={task.status.value}")
            await self.notifier.send(
                f"♻️ Возобновление задачи #{task.id} с шага {task.current_step}"
            )
            await self._queue.put(task)
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
