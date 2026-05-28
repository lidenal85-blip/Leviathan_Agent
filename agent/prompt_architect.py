"""
agent/prompt_architect.py — Анализ запроса, декомпозиция, оптимизация промта.

Цепочка:
  1. Оценить сложность: простая/сложная
  2. Если сложная → быстрый LLM-анализ (Groq llama-3.1-8b-instant)
  3. Возвращает ArchitectPlan с улучшенным промтом и шагами
  4. Простые задачи → сразу в работу, без анализа
Анти-галлюцинация: активна
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("prompt_architect")

# ── Критерии сложных задач ───────────────────────────────────

COMPLEX_WORDS = {
    # Продукт
    "учебник", "книга", "сайт", "платформа", "портал",
    # Автоматизация
    "система", "мониторинг", "пайплайн", "автоматизируй",
    # Код
    "архитектура", "рефактор", "миграция",
    # Деплой
    "deploy", "задеплой", "настрой", "setup",
    # Контент
    "guide", "docs", "документация", "руководство",
    # Глаголы-триггеры
    "create", "build", "создай", "напиши", "разработай",
    "сделай", "реализуй", "построй",
}

SIMPLE_PATTERNS = [
    r'^(echo|ls|cat|ps|top|df|du|curl|grep)\b',  # Баш-одна команда
    r'^(status|health|info|ping)\b',              # Инфо-запросы
    r'^\?\s+',                                     # Вопрос
    r'^(what|how|why|when|where)\b',
    r'^(что|как|почему|где|когда)\s',
]

TASK_TYPES = {
    "code":       ["код", "скрипт", "python", "class", "function", "модуль", "fix", "исправь"],
    "product":    ["сайт", "учебник", "платформа", "html", "ui", "дизайн"],
    "automation": ["система", "монитор", "авто", "deploy", "pipeline", "schedule"],
    "research":   ["анализ", "исследовачом", "сравни", "выбери"],
    "learning":   ["объясни", "расскажи", "guide", "обучение"],
}


@dataclass
class ArchitectPlan:
    """Pезультат анализа запроса."""
    original:         str             # Исходный запрос
    improved_prompt:  str             # Улучшенная формулировка
    task_type:        str             # code | product | automation | research | learning
    is_complex:       bool            # True → показать план
    steps:            list[str]       # Шаги выполнения
    risks:            list[str]       # Риски / слабые места
    estimate:         str = ""        # Оценка времени
    verdict:          str = "viable"  # viable | needs_simplification | unrealistic


class PromptArchitect:
    """
    Анализирует запрос пользователя, возвращает ArchitectPlan.
    Быстрый анализ — Groq llama-3.1-8b-instant (< 2с).
    Для простых задач — локальный анализ без LLM.
    """

    def __init__(self, llm_pool=None) -> None:
        self.pool = llm_pool

    # ── Публичный метод ──────────────────────────────────────────

    async def analyze(self, user_input: str) -> ArchitectPlan:
        """
        Главный метод. Возвращает ArchitectPlan.
        Быстрый путь: is_complex=False → без LLM.
        Полный путь: is_complex=True → LLM анализ.
        """
        text = user_input.strip()

        # Быстрая проверка: простая задача?
        if self._is_simple(text):
            return ArchitectPlan(
                original=text,
                improved_prompt=text,
                task_type=self._detect_type(text),
                is_complex=False,
                steps=[],
                risks=[],
            )

        # Сложная задача — запрашиваем LLM
        if self.pool:
            try:
                return await self._llm_analyze(text)
            except Exception as e:
                logger.warning("Architect LLM ошибка: %s, фоллбэк на local", e)

        return self._local_analyze(text)

    # ── Анализ через LLM (Groq — быстро) ──────────────────────

    async def _llm_analyze(self, text: str) -> ArchitectPlan:
        SYSTEM = (
            "You are Prompt Architect. Analyze the user task. "
            "Reply ONLY with valid JSON, no markdown, no extra text.\n"
            "Schema: {\"task_type\": string, \"verdict\": string, "
            "\"improved_prompt\": string, \"steps\": [string], "
            "\"risks\": [string], \"estimate\": string}"
        )
        USER = (
            f"Task: \"{text}\"\n\n"
            "Rules:\n"
            "- task_type: code|product|automation|research|learning\n"
            "- verdict: viable|needs_simplification|unrealistic\n"
            "- improved_prompt: clear, actionable, no fluff (same language as input)\n"
            "- steps: 3-5 concrete actions (same language as input)\n"
            "- risks: 1-2 critical risks (same language as input), empty if none\n"
            "- estimate: rough time estimate (e.g. '5 min', '30 min', '2 hours')\n"
            "Anti-hallucination: only facts, mark unknowns as '?'"
        )
        result = await self.pool.complete(
            USER,
            system=SYSTEM,
            max_tokens=400,
            prefer_provider="groq",   # Быстро и дешево
        )
        data = self._parse_json(result)
        return ArchitectPlan(
            original         = text,
            improved_prompt  = data.get("improved_prompt", text),
            task_type        = data.get("task_type", "code"),
            is_complex       = True,
            steps            = data.get("steps", []),
            risks            = data.get("risks", []),
            estimate         = data.get("estimate", ""),
            verdict          = data.get("verdict", "viable"),
        )

    # ── Локальный анализ (без LLM) ────────────────────────────

    def _local_analyze(self, text: str) -> ArchitectPlan:
        """Hевристическая декомпозиция без LLM."""
        task_type = self._detect_type(text)
        steps = self._generate_steps(text, task_type)
        return ArchitectPlan(
            original        = text,
            improved_prompt = self._improve_local(text),
            task_type       = task_type,
            is_complex      = True,
            steps           = steps,
            risks           = self._detect_risks(text),
            estimate        = self._estimate_time(steps),
            verdict         = "viable",
        )

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _is_simple(text: str) -> bool:
        words = text.split()
        if len(words) <= 5:
            return True
        tl = text.lower()
        for p in SIMPLE_PATTERNS:
            if re.match(p, tl):
                return True
        return not any(w in tl for w in COMPLEX_WORDS)

    @staticmethod
    def _detect_type(text: str) -> str:
        tl = text.lower()
        for t, kws in TASK_TYPES.items():
            if any(k in tl for k in kws):
                return t
        return "code"

    @staticmethod
    def _detect_risks(text: str) -> list[str]:
        risks = []
        tl = text.lower()
        if any(w in tl for w in ["api", "ключ", "token", "auth"]):
            risks.append("Требуется API-ключ или токен")
        if any(w in tl for w in ["deploy", "сервер", "задеплой"]):
            risks.append("Нужен доступ к серверу")
        if any(w in tl for w in ["база", "db", "sql", "postgres", "sqlite"]):
            risks.append("Изменения БД — сделай бэкаперед")
        return risks[:2]

    @staticmethod
    def _generate_steps(text: str, task_type: str) -> list[str]:
        tl = text.lower()
        templates = {
            "product": [
                "Определить структуру и страницы",
                "Создать HTML/CSS шаблон",
                "Заполнить контентом",
                "Проверить навигацию",
                "Запустить / опубликовать",
            ],
            "automation": [
                "Определить входные данные и триггеры",
                "Написать Python-скрипт",
                "Добавить обработку ошибок",
                "Настроить расписание / systemd",
                "Проверить в работе",
            ],
            "code": [
                "Прочитать задачу и уточнить результат",
                "Написать код",
                "Добавить тесты",
                "Запустить и проверить",
            ],
            "research": [
                "Собрать данные",
                "Проанализировать",
                "Сформулировать выводы",
            ],
            "learning": [
                "Определить структуру",
                "Создать материалы",
                "Добавить примеры",
                "Опубликовать",
            ],
        }
        return templates.get(task_type, templates["code"])

    @staticmethod
    def _improve_local(text: str) -> str:
        """Mинимальное улучшение без LLM."""
        t = text.strip()
        if not t.endswith(".") and len(t.split()) > 5:
            t += ". Верни только проверенные факты."
        return t

    @staticmethod
    def _estimate_time(steps: list[str]) -> str:
        n = len(steps)
        if n <= 2: return "~5 мин"
        if n <= 4: return "~15-30 мин"
        return "~30-60 мин"

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Hадёжный JSON-парсер, убирает маркдаун."""
        cleaned = re.sub(r"```[\w]*\n?", "", text).strip()
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Architect: JSON не удалось спарсить: %s", text[:100])
        return {}


# ── Объект для TG-обработчика ──────────────────────────────

def format_plan_message(plan: ArchitectPlan) -> str:
    """Форматирует план для TG-сообщения."""
    type_icons = {
        "code": "💻", "product": "🌐", "automation": "⚙️",
        "research": "🔍", "learning": "📚",
    }
    verdict_icons = {
        "viable": "✅", "needs_simplification": "⚠️", "unrealistic": "❌"
    }
    icon     = type_icons.get(plan.task_type, "🔧")
    verdict  = verdict_icons.get(plan.verdict, "•")
    estimate = f" | ~{plan.estimate}" if plan.estimate else ""

    lines = [
        f"{icon} <b>Plan: {plan.task_type}</b> {verdict}{estimate}\n",
    ]

    if plan.improved_prompt != plan.original:
        lines.append(f"💬 <b>Задача:</b>\n<i>{plan.improved_prompt}</i>\n")
    else:
        lines.append(f"💬 <b>Задача:</b> <i>{plan.original}</i>\n")

    if plan.steps:
        lines.append("📋 <b>Шаги:</b>")
        for i, s in enumerate(plan.steps, 1):
            lines.append(f"  {i}. {s}")
        lines.append("")

    if plan.risks:
        lines.append("⚠️ <b>Риски:</b> " + " | ".join(plan.risks))

    return "\n".join(lines)