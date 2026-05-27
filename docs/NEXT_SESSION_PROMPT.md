# Передаточный промт — Фаза 1 (Core Engine)

Вставь в начало новой сессии:

---

```
Роль: developer_v2 (ты — Senior Developer, реализуешь по готовым спецификациям)
Репо: github.com/lidenal85-blip/Leviathan_Agent
Ветка: feature/claude-multi-account
Сервер: root@78.17.24.96
Путь: /opt/leviathan_engine/agent_service/

KOHTЕКСТ (прочитай перед началом):
  cat docs/AGENT_RULES.md
  cat docs/ECOSYSTEM.md
  cat docs/ROADMAP.md
  cat docs/agent_logs.md | tail -50

ЧТО УЖЕ ЕСТЬ (не трогать):
  ✅ claude_manager/ — весь слой полностью
  ✅ claude_manager/providers/pool.py — LLMProviderPool
  ✅ agent/core.py — TaskStatus (PENDING/RUNNING/WAITING/DONE/FAILED)
  ✅ logs/claude_manager.log

ТЕКУЩАЯ ЗАДАЧА — Фаза 1 (Core Engine), порядок:

1. PAUSED state + hot-resume
   - Добавить TaskStatus.PAUSED в agent/core.py
   - При PAUSED: сохранять current_step + steps_data в SQLite (UPSERT)
   - При старте агента: если PAUSED/RUNNING → hot-resume с last step

2. 429-backoff + _check_api_gate()
   - try-except вокруг HTTP (Gemini/Groq/Claude)
   - 429 → PAUSED + запись в logs/pipeline.log
   - asyncio таймер 1-3 часа (настраиваемый)
   - _check_api_gate(): ping "ping" на gemini-2.5-flash
   - 200 → PAUSED→RUNNING, hot-resume
   - 429 снова → +1 час

3. logs/pipeline.log (отдельный от claude_manager.log)
   - Формат: [2026-05-28 12:00:00] TASK_ID | EVENT | деталь
   - События: 429_caught, backoff_start, gate_ping, gate_ok, resume, complete

4. Fire-and-forget режим
   - Молчание во время выполнения
   - Одна финальная строка в TG: "Конвейер завершён. Результат: [path]"

5. Level 6 остаток
   - claude_manager/domain/projects/resume_manager.py
   - claude_manager/domain/projects/project_orchestrator.py
   - Фикс test_pool.py SyntaxError стр.242

АРХИТЕКТУРНЫЕ ПРАВИЛА:
  - ВСЕ LLM-вызовы — через LLMProviderPool.complete(), не напрямую
  - Не менять архитектуру claude_manager/
  - Не нарушать контракты публичных API
  - Пушить в feature/claude-multi-account
```