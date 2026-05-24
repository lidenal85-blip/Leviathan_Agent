"""
agent/model_router.py — роутер выбора AI-провайдера и режима thinking
═══════════════════════════════════════════════════════════════════════

Режимы (MODEL_MODE в .env):
  GEMINI_ONLY         Только Gemini FC-loop. Быстро, дёшево.
  CLAUDE_ONLY         Только Claude CLI. Качество, когда Gemini лимиты кончились.
  GEMINI_THINK_CLAUDE Gemini ведёт loop, Claude — для сложных шагов.
  CLAUDE_THINK_GEMINI Claude планирует (thinking), Gemini исполняет bash/git/файлы.
  AUTO                Агент сам решает по содержимому промта.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("model_router")


# ── Режимы ────────────────────────────────────────────────────────────────────

class ModelMode(str, Enum):
    GEMINI_ONLY         = "GEMINI_ONLY"
    CLAUDE_ONLY         = "CLAUDE_ONLY"
    GEMINI_THINK_CLAUDE = "GEMINI_THINK_CLAUDE"
    CLAUDE_THINK_GEMINI = "CLAUDE_THINK_GEMINI"
    AUTO                = "AUTO"


@dataclass(frozen=True)
class RouteDecision:
    provider:      str   # "gemini" | "claude"
    use_thinking:  bool
    reason:        str   # для лога — почему выбрали этот провайдер

    def __str__(self) -> str:
        t = " +thinking" if self.use_thinking else ""
        return f"{self.provider}{t} ({self.reason})"


# ── Ключевые слова ────────────────────────────────────────────────────────────

# Промты, где Claude справится лучше — аналитика, архитектура, ревью
CLAUDE_KEYWORDS: list[str] = [
    # Архитектура и дизайн
    "архитектур", "спроектируй", "декомпозиц", "architecture",
    "structure", "design pattern", "как правильно организовать",
    # Аудит и ревью
    "аудит", "code review", "проверь код", "найди проблемы",
    "что не так", "почему падает", "дебаг", "debug",
    # Аналитика
    "объясни почему", "что лучше", "сравни подходы", "compare",
    "риски", "проблемы с", "post_mortem", "расследуй",
    # Документация
    "документац", "напиши доку", "опиши как работает",
    # Роли из Arbitr pipeline
    "[DECOMPOSER]", "[ARCHITECT]", "[AUDITOR]", "[REVIEWER]",
]

# Промты, где Gemini быстрее — операции, bash, файлы, git
GEMINI_KEYWORDS: list[str] = [
    # Операции
    "запусти", "задеплой", "перезапусти", "restart",
    "проверь что работает", "мониторинг", "статус",
    # Git и файлы
    "сделай git", "сделай commit", "напиши файл", "создай файл",
    "удали", "скопируй", "переименуй", "chmod",
    # Bash и система
    "выполни", "установи", "install", "apt", "pip",
    "bash", "shell", "systemctl", "nginx", "curl", "wget",
    # Быстрые проверки
    "покажи логи", "tail", "grep", "ps aux", "df -h",
]

# Когда нужен extended thinking Claude
THINKING_KEYWORDS: list[str] = [
    "архитектур", "спроектируй", "декомпозиц",
    "что лучше", "как правильно", "выбери подход", "choose",
    "риски", "проблемы с", "почему не работает", "why",
    "оптимизируй алгоритм", "сложная", "complex",
]


# ── Роутер ────────────────────────────────────────────────────────────────────

class ModelRouter:

    def __init__(self, default_mode: ModelMode = ModelMode.AUTO):
        self.default_mode = default_mode

    def route(
        self,
        prompt:     str,
        mode:       ModelMode | str | None = None,
        step_index: int = 0,
    ) -> RouteDecision:
        """
        Возвращает RouteDecision с выбором провайдера и режима thinking.

        Args:
            prompt:     текст задачи / шага
            mode:       явный режим; если None — используется default_mode
            step_index: номер шага в итерации (0 = первый шаг = планирование)
        """
        if mode is None:
            mode = self.default_mode

        if isinstance(mode, str):
            try:
                mode = ModelMode(mode)
            except ValueError:
                logger.warning("Неизвестный режим '%s', используем AUTO", mode)
                mode = ModelMode.AUTO

        text = prompt.lower()

        # Быстрые пути для фиксированных режимов
        if mode == ModelMode.GEMINI_ONLY:
            return RouteDecision("gemini", False, "GEMINI_ONLY mode")

        if mode == ModelMode.CLAUDE_ONLY:
            use_t = any(k.lower() in text for k in THINKING_KEYWORDS)
            return RouteDecision("claude", use_t, "CLAUDE_ONLY mode")

        # Скоринг
        claude_score = sum(1 for k in CLAUDE_KEYWORDS  if k.lower() in text)
        gemini_score = sum(1 for k in GEMINI_KEYWORDS  if k.lower() in text)
        use_thinking = any(k.lower() in text for k in THINKING_KEYWORDS)

        if mode == ModelMode.GEMINI_THINK_CLAUDE:
            # Gemini ведёт loop; Claude только если явно аналитика
            if claude_score >= 2:
                return RouteDecision(
                    "claude", use_thinking,
                    f"GEMINI_THINK_CLAUDE: claude_score={claude_score}"
                )
            return RouteDecision("gemini", False, "GEMINI_THINK_CLAUDE: default gemini")

        if mode == ModelMode.CLAUDE_THINK_GEMINI:
            # Claude планирует; Gemini только для чистых execution-шагов
            if gemini_score > claude_score and gemini_score >= 2:
                return RouteDecision(
                    "gemini", False,
                    f"CLAUDE_THINK_GEMINI: execution step, gemini_score={gemini_score}"
                )
            return RouteDecision(
                "claude", use_thinking,
                f"CLAUDE_THINK_GEMINI: planning step, claude_score={claude_score}"
            )

        # ── AUTO ──────────────────────────────────────────────────────────────
        # Первый шаг (планирование) — всегда через Claude если скор одинаковый
        if step_index == 0 and claude_score == gemini_score:
            use_t = use_thinking or len(prompt) > 300
            return RouteDecision(
                "claude", use_t,
                "AUTO: step_index=0, planning → claude"
            )

        if claude_score > gemini_score:
            return RouteDecision(
                "claude", use_thinking,
                f"AUTO: claude_score={claude_score} > gemini_score={gemini_score}"
            )

        if gemini_score > claude_score:
            return RouteDecision(
                "gemini", False,
                f"AUTO: gemini_score={gemini_score} > claude_score={claude_score}"
            )

        # Ничья → Claude для длинных промтов, Gemini для коротких
        if len(prompt) > 300:
            return RouteDecision("claude", use_thinking, "AUTO: tie, long prompt → claude")

        return RouteDecision("gemini", False, "AUTO: tie, short prompt → gemini")


# ── Глобальный инстанс ────────────────────────────────────────────────────────

_router: ModelRouter | None = None


def get_router(mode: str | None = None) -> ModelRouter:
    global _router
    if _router is None or mode is not None:
        _router = ModelRouter(ModelMode(mode or "AUTO"))
    return _router
