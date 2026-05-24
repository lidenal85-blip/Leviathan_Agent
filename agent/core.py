"""
agent/core.py — ядро LEVIATHAN AGENT
Gemini function calling loop с логированием каждого шага.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import google.generativeai as genai

from agent.key_pool import GeminiKeyPool
from agent.tools import TOOLS_REGISTRY, GEMINI_TOOLS, is_dangerous

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — LEVIATHAN AGENT, автономный DevOps-агент на сервере leviathanstory.ru.

ТВОИ ПРОЕКТЫ:
- VoiceStudio: /var/www/voicestudio (порт 8120) — аудио обработка, FastAPI
- KinoVibe: /var/www/kinovibe (порт 8110) — фильм-матчер, Flutter + FastAPI
- AI Outreach: /opt/ai_outreach (порт 8000) — мультиагентная outreach система
- Orionyx: /opt/orionyx (порт 8005) — инвестиционная платформа
- LEVIATHAN Engine: /opt/leviathan_engine — ядро экосистемы
- Домен: leviathanstory.ru (nginx, SSL certbot)
- GitHub: github.com/lidenal85-blip

ПРАВИЛА РАБОТЫ:
1. Перед изменением файла — ВСЕГДА читай его через read_file
2. После изменений — проверяй что сервис работает (curl health check)
3. Каждый шаг логируй кратко в виде: "🔍 Читаю...", "✏️ Пишу...", "✅ Готово"
4. В конце задачи — пуш на GitHub с осмысленным commit message
5. НЕ останавливай работающие сервисы без явного указания
6. Опасные операции (rm -rf, DROP TABLE) — сначала спрашивай

СТИЛЬ ОТВЕТОВ:
- Краткие промежуточные сообщения (1-2 строки)
- Финальный отчёт: что сделано, что изменено, ссылки
- Если что-то пошло не так — честно объясни и предложи план Б
"""


class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    WAITING   = "waiting_approval"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    idx: int
    tool: str
    args: dict
    result: dict | None = None
    ts: float = field(default_factory=time.time)
    duration: float = 0.0


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt: str = ""
    status: TaskStatus = TaskStatus.PENDING
    steps: list[TaskStep] = field(default_factory=list)
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    mode: str = "NORMAL"
    pending_approval: dict | None = None


class LeviathanAgent:
    """
    Основной класс агента.
    Gemini function calling loop до MAX_ITERATIONS итераций.
    """

    def __init__(
        self,
        key_pool: GeminiKeyPool,
        max_iterations: int = 50,
        on_step: Callable | None = None,
        on_approval_needed: Callable | None = None,
    ) -> None:
        self.key_pool = key_pool
        self.max_iterations = max_iterations
        self.on_step = on_step                       # callback: (task, step) → None
        self.on_approval_needed = on_approval_needed  # callback: (task, cmd) → bool

    def _build_model(self, key: str) -> genai.GenerativeModel:
        genai.configure(api_key=key)
        return genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM_PROMPT,
            tools=[{"function_declarations": GEMINI_TOOLS}],
        )

    async def run(self, task: Task) -> Task:
        """Запускаем задачу. Возвращает Task с результатом."""
        task.status = TaskStatus.RUNNING
        logger.info("Agent: задача %s начата: %s", task.id, task.prompt[:80])

        messages = [{"role": "user", "parts": [task.prompt]}]

        for iteration in range(self.max_iterations):
            key = await self.key_pool.get_key()
            try:
                model = self._build_model(key)
                chat = model.start_chat(history=messages[:-1])
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: chat.send_message(messages[-1]["parts"])
                )
                self.key_pool.mark_ok(key)

            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower():
                    self.key_pool.mark_rate_limited(key)
                    logger.warning("Agent: 429 на ключ, пробуем следующий")
                    continue
                task.status = TaskStatus.FAILED
                task.error = f"Gemini ошибка: {err_str}"
                logger.error("Agent: %s", task.error)
                return task

            # Разбираем ответ
            candidate = response.candidates[0]
            parts = candidate.content.parts

            has_tool_call = False
            tool_results = []

            for part in parts:
                # Текстовый вывод
                if hasattr(part, "text") and part.text:
                    logger.info("Agent [%d]: %s", iteration, part.text[:100])
                    # Финальный ответ если нет вызовов инструментов
                    if len(parts) == 1:
                        task.status = TaskStatus.DONE
                        task.result = part.text
                        task.finished_at = time.time()
                        return task

                # Вызов инструмента
                if hasattr(part, "function_call") and part.function_call:
                    has_tool_call = True
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info("Agent [%d]: вызов %s(%s)", iteration, tool_name, str(tool_args)[:80])

                    # Проверка опасных команд
                    if tool_name == "bash_tool" and is_dangerous(tool_args.get("cmd", "")):
                        if task.mode != "FULL":
                            if self.on_approval_needed:
                                approved = await self.on_approval_needed(task, tool_args.get("cmd", ""))
                                if not approved:
                                    tool_result = {"error": "Операция отклонена пользователем", "ok": False}
                                    tool_results.append((tool_name, tool_result))
                                    continue
                            else:
                                tool_result = {
                                    "error": f"Опасная операция требует подтверждения: {tool_args.get('cmd')}",
                                    "ok": False
                                }
                                tool_results.append((tool_name, tool_result))
                                continue

                    # Выполняем инструмент
                    step = TaskStep(idx=len(task.steps), tool=tool_name, args=tool_args)
                    t0 = time.time()

                    tool_fn = TOOLS_REGISTRY.get(tool_name)
                    if tool_fn:
                        try:
                            tool_result = await tool_fn(**tool_args)
                        except Exception as e:
                            tool_result = {"error": str(e), "ok": False}
                    else:
                        tool_result = {"error": f"Инструмент '{tool_name}' не найден", "ok": False}

                    step.result = tool_result
                    step.duration = time.time() - t0
                    task.steps.append(step)

                    if self.on_step:
                        asyncio.create_task(self.on_step(task, step))

                    tool_results.append((tool_name, tool_result))

            # Добавляем результаты инструментов в историю
            if has_tool_call and tool_results:
                # Добавляем ответ модели
                messages.append({"role": "model", "parts": parts})
                # Добавляем результаты
                function_responses = []
                for tool_name, tool_result in tool_results:
                    function_responses.append({
                        "function_response": {
                            "name": tool_name,
                            "response": {"result": json.dumps(tool_result, ensure_ascii=False)},
                        }
                    })
                messages.append({"role": "user", "parts": function_responses})

        # Превысили лимит итераций
        task.status = TaskStatus.FAILED
        task.error = f"Превышен лимит итераций ({self.max_iterations})"
        task.finished_at = time.time()
        return task
