"""
agent/intent.py — Intent Detection для Leviathan Agent.
Определяет намерение пользователя по триггерным словам.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Intent:
    name: str
    confidence: float = 1.0
    meta: dict = field(default_factory=dict)


TRIGGERS = {
    # 1. Доставка файла
    "deliver_file": [
        r"\b(отчёт|отчет|файл|архив|скачай|скачать|пришли|пришлёшь|отправь"
        r"|покажи файл|дай файл|дай архив|zip|pack)\b"
    ],
    # 2. Обратиться к памяти
    "recall_memory": [
        r"\b(помнишь|помнит|раньше|прошлый раз|в прошлый раз|как мы делали"
        r"|что делали|ранее|предыдущий|прошлая задача|уже делал)\b"
    ],
    # 3. Статус / сводка
    "status_report": [
        r"\b(статус|как дела|что сделал|итог|итоги|сводка|отчитайся"
        r"|что было|что происходит|прогресс)\b"
    ],
    # 4. Отладка ошибки
    "debug_error": [
        r"\b(ошибка|не работает|сломалось|баг|bug|fix|фикс|упал|падает"
        r"|exception|traceback|error|failed|failure)\b"
    ],
    # 5. Деплой / рестарт
    "deploy": [
        r"\b(задеплой|деплой|deploy|запусти|запустить|рестарт|restart"
        r"|перезапусти|перезапустить|поднять|поднять сервис)\b"
    ],
    # 6. Git операции
    "git_ops": [
        r"\b(закоммить|коммит|commit|запуши|push|пуш|сохрани в гит"
        r"|git add|git push|залей|обнови репо|синхронизируй)\b"
    ],
    # 7. Только анализ (не трогать файлы)
    "analyze_only": [
        r"\b(проанализируй|изучи|оцени|посмотри|проверь|аудит|audit"
        r"|разбери|объясни|что за|как устроен)\b"
    ],
    # 8. Конкретный проект
    "project_context": [
        r"\b(kinovibe|arbitr|voicestudio|voice.studio|orionyx|leviathan"
        r"|ai.?outreach|arbitrcockpit)\b"
    ],
    # 9. Срочно / коротко
    "urgent": [
        r"\b(срочно|срочная|быстро|быстрее|сейчас же|одной командой"
        r"|максимально кратко|без лишних слов)\b"
    ],
    # 10. Токены / лимиты
    "token_stats": [
        r"\b(токены|токен|token|ключи|ключ|лимит|лимиты|расход|расходы"
        r"|сколько потратил|статистика ключей|stats)\b"
    ],
}

# Проекты на сервере
PROJECT_NAMES = {
    "kinovibe":      {"port": 8110, "path": "/opt/kinovibe"},
    "arbitr":        {"port": 8095, "path": "/opt/arbitr_cockpit"},
    "voicestudio":   {"port": 8120, "path": "/opt/voicestudio"},
    "voice.studio":  {"port": 8120, "path": "/opt/voicestudio"},
    "orionyx":       {"port": 8005, "path": "/opt/orionyx"},
    "ai_outreach":   {"port": 8000, "path": "/opt/ai_outreach"},
    "leviathan":     {"port": 8200, "path": "/opt/leviathan_engine/agent_service"},
}


def detect_intents(text: str) -> list[Intent]:
    """Определить намерения пользователя из текста."""
    text_lower = text.lower()
    intents = []

    for intent_name, patterns in TRIGGERS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                meta = {}
                if intent_name == "project_context":
                    for proj, info in PROJECT_NAMES.items():
                        if proj in text_lower:
                            meta["project"] = proj
                            meta["info"] = info
                            break
                intents.append(Intent(name=intent_name, meta=meta))
                break

    return intents


def build_intent_prefix(text: str) -> str:
    """
    Строит префикс для системного промта на основе интентов.
    Вставляется перед задачей пользователя в LeviathanAgent.
    """
    intents = detect_intents(text)
    if not intents:
        return ""

    lines = ["[INTENT DETECTION]"]
    for intent in intents:
        if intent.name == "deliver_file":
            lines.append(
                "• ДОСТАВКА: после выполнения — pack_to_zip → send_file_to_tg, "
                "не отвечай длинным текстом"
            )
        elif intent.name == "recall_memory":
            lines.append("• ПАМЯТЬ: НАЧНИ с kb_search по теме задачи")
        elif intent.name == "status_report":
            lines.append(
                "• СТАТУС: только сводка из kb_search, "
                "не выполняй новых действий"
            )
        elif intent.name == "debug_error":
            lines.append(
                "• ОТЛАДКА: сначала journalctl / cat error log, "
                "потом анализируй, потом фикс"
            )
        elif intent.name == "deploy":
            lines.append(
                "• ДЕПЛОЙ: systemctl restart → sleep 3 → health check → "
                "сообщи результат"
            )
        elif intent.name == "git_ops":
            lines.append(
                "• GIT: git add -A → commit → push → проверь статус"
            )
        elif intent.name == "analyze_only":
            lines.append(
                "• АНАЛИЗ: только читай файлы, НЕ изменяй, НЕ пиши"
            )
        elif intent.name == "project_context":
            proj = intent.meta.get("project", "")
            info = intent.meta.get("info", {})
            lines.append(
                f"• ПРОЕКТ: {proj} | порт {info.get('port','?')} | "
                f"путь {info.get('path','?')}"
            )
        elif intent.name == "urgent":
            lines.append(
                "• СРОЧНО: минимум итераций, одна команда, "
                "краткий ответ"
            )
        elif intent.name == "token_stats":
            lines.append(
                "• ТОКЕНЫ: вызови get_token_stats и пришли результат"
            )
    lines.append("")
    return "\n".join(lines)
