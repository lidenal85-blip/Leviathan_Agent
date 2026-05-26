"""TaskPlanner — разбивает цель на шаги через LLMProviderPool."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from claude_manager.logger import StepLogger

if TYPE_CHECKING:
    from claude_manager.providers.pool import LLMProviderPool

_log = StepLogger("task_planner")

DECOMPOSE_PROMPT = """\
Ты планировщик задач. Разбей цель на конкретные исполняемые шаги.

Цель: {goal}

TREБОВАНИЯ:
- От 5 до 15 шагов
- Каждый шаг — одно действие, конкретное и выполнимое
- Шаги последовательные, каждый опирается на результат предыдущего

ОТВЕТИ ТОЛЬКО JSON-массивом без пояснений:
["\u0428аг 1...", "\u0428аг 2...", ...]
"""


class TaskPlanner:
    def __init__(self, pool: "LLMProviderPool"):
        self._pool = pool

    async def decompose(self, goal: str, session_id: str = "") -> list[str]:
        """Returns list of step descriptions."""
        _log.task(f"декомпозиция цели: {goal[:80]}")
        prompt = DECOMPOSE_PROMPT.format(goal=goal)
        _log.step("запрос к LLMProviderPool")
        response = await self._pool.complete(
            prompt=prompt,
            session_id=session_id or "planner",
            system="Отвечай только валидным JSON-массивом строк. Без markdown, без пояснений.",
        )
        steps = self._parse(response)
        _log.result(f"декомпозиция готова: {len(steps)} шагов")
        _log.next("передаём шаги в ProjectExecutor")
        return steps

    def _parse(self, text: str) -> list[str]:
        """Extract JSON array from LLM response, tolerant to markdown wrapping."""
        # чистим markdown-блоки
        text = re.sub(r"```[\w]*", "", text).strip()
        # ищем первый JSON-массив
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            _log.error(f"не удалось распарсить JSON: {text[:200]}")
            # fallback: разбиваем по строкам
            lines = [l.strip(" -\t") for l in text.splitlines() if l.strip()]
            return lines[:15] or ["Execute: " + text[:200]]
        try:
            steps = json.loads(match.group())
            if isinstance(steps, list):
                return [str(s) for s in steps][:15]
        except json.JSONDecodeError as e:
            _log.error(f"JSONDecodeError: {e}")
        return [text[:500]]