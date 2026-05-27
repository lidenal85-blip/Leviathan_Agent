"""
execution/backoff_scheduler.py — Автоматический backoff при 429.

Alгоритм:
  1. Все ключи rate-limited → BackoffScheduler.pause_and_wait(task)
  2. Задача → PAUSED, фоновый asyncio-таймер
  3. Через BACKOFF_HOURS (1-3) → _check_api_gate()
  4. Gate OK → task.status → RUNNING, asyncio.Event() сигнализирует возобновление
  5. Gate заблокирован → +1 час, повторяем
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

from execution.pipeline_log import PipelineEvent, plog

if TYPE_CHECKING:
    from agent.core import Task

logger = logging.getLogger("backoff")

BACKOFF_HOURS_DEFAULT: int = 1   # из settings если есть
BACKOFF_HOURS_MAX: int = 3


async def _check_api_gate(gemini_key: str, groq_key: str = "") -> bool:
    """
    Дешёвый ping-проверочный запрос для проверки API.
    Возвращает True если хотя бы один провайдер доступен.
    """
    import httpx

    # Пробуем Gemini
    if gemini_key:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={gemini_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 1},
            }
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload)
            if r.status_code == 200:
                logger.info("Gate: Gemini OK")
                return True
            logger.info("Gate: Gemini %d", r.status_code)
        except Exception as e:
            logger.warning("Gate: Gemini ошибка: %s", e)

    # Пробуем Groq как запасной вариант
    if groq_key:
        try:
            import groq as groq_sdk
            client = groq_sdk.AsyncGroq(api_key=groq_key)
            await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            logger.info("Gate: Groq OK")
            return True
        except Exception as e:
            logger.warning("Gate: Groq ошибка: %s", e)

    return False


class BackoffScheduler:
    """
    Управляет backoff-циклами для задач. Один экземпляр на агент.
    """

    def __init__(self, key_pool) -> None:
        self.key_pool = key_pool
        self._waiters: dict[str, asyncio.Event] = {}  # task_id → event

    def _get_first_available_key(self) -> tuple[str, str]:
        """Возвращает (gemini_key, groq_key) для gate-проверки."""
        from config.settings import get_settings
        settings = get_settings()

        # Первый не rate-limited Gemini ключ
        gemini_key = ""
        for k in self.key_pool._keys if hasattr(self.key_pool, "_keys") else []:
            gemini_key = k
            break

        # Первый Groq ключ
        groq_key = next(
            (getattr(settings, f"GROQ_K{i}", "") for i in range(1, 6)
             if getattr(settings, f"GROQ_K{i}", "").strip()),
            ""
        )
        return gemini_key, groq_key

    async def pause_and_wait(self, task: "Task", storage=None) -> None:
        """
        Переводим task в PAUSED и возобновляемся когда API доступен.
        Блокирует вызывающую корутину до возобновления.
        """
        from agent.core import TaskStatus

        task.status = TaskStatus.PAUSED
        task.paused_at = time.time()
        plog(task.id, PipelineEvent.TASK_PAUSED,
             f"шаг={len(task.steps)} reason=all_keys_rate_limited")

        if storage:
            await storage.save(task)

        event = asyncio.Event()
        self._waiters[task.id] = event

        # Запускаем фоновый поллинг (не блокирует цикл событий)
        asyncio.create_task(self._poll_loop(task, event, storage))

        # Блокируем цикл loop пока не возобновимся
        await event.wait()
        logger.info("BackoffScheduler: задача %s возобновлена", task.id)

    async def _poll_loop(
        self,
        task: "Task",
        event: asyncio.Event,
        storage,
    ) -> None:
        """backoff-цикл: спим → ping → повтор."""
        from agent.core import TaskStatus
        from config.settings import get_settings

        settings = get_settings()
        backoff_hours = getattr(settings, "BACKOFF_HOURS", BACKOFF_HOURS_DEFAULT)
        backoff_hours = max(1, min(backoff_hours, BACKOFF_HOURS_MAX))
        gemini_key, groq_key = self._get_first_available_key()

        attempt = 0
        while True:
            sleep_sec = backoff_hours * 3600
            plog(task.id, PipelineEvent.BACKOFF_START,
                 f"попытка={attempt+1} sleep={backoff_hours}h")
            logger.info("BackoffScheduler: спим %dh (задача %s)", backoff_hours, task.id)
            await asyncio.sleep(sleep_sec)

            plog(task.id, PipelineEvent.GATE_PING, f"gemini={'yes' if gemini_key else 'no'} groq={'yes' if groq_key else 'no'}")
            ok = await _check_api_gate(gemini_key, groq_key)

            if ok:
                plog(task.id, PipelineEvent.GATE_OK, "API доступен")
                task.status = TaskStatus.RUNNING
                task.paused_at = 0.0
                if storage:
                    await storage.save(task)
                event.set()
                self._waiters.pop(task.id, None)
                return
            else:
                attempt += 1
                plog(task.id, PipelineEvent.GATE_BLOCKED,
                     f"попытка={attempt} продляем +{backoff_hours}h")
                # Увеличиваем окно до максимума
                if backoff_hours < BACKOFF_HOURS_MAX:
                    backoff_hours += 1
                    logger.info(
                        "BackoffScheduler: увеличиваем окно до %dh", backoff_hours
                    )