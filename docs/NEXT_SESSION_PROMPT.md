# Передаточный промт — Level 6

Отправь в начале новой сессии:

---

```
Роль: developer_v2 (ты — Senior Developer, реализуешь по готовым спецификациям)
Репо: github.com/lidenal85-blip/Leviathan_Agent
Ветка: feature/claude-multi-account
Последний коммит: f932ca0
Сервер: root@78.17.24.96
Путь: /opt/leviathan_engine/agent_service/

CLAUDE MANAGER — что уже есть (не трогать):
  ✅ claude_manager/logger.py              — StepLogger
  ✅ claude_manager/core/crypto/           — CryptoKeyManager
  ✅ claude_manager/core/storage/account_store.py
  ✅ claude_manager/core/storage/advisory_lock.py
  ✅ claude_manager/domain/accounts/lifecycle_manager.py
  ✅ claude_manager/domain/sessions/context_manager.py
  ✅ claude_manager/providers/claude/adapter.py
  ✅ claude_manager/providers/pool.py      — LLMProviderPool + AllAccountsRateLimited

TEKUЩАЯ ЗАДАЧА — Level 6, порядок:
  1. claude_manager/core/storage/project_store.py
  2. claude_manager/domain/projects/task_planner.py
  3. claude_manager/domain/projects/project_executor.py
  4. claude_manager/domain/projects/resume_manager.py
  5. claude_manager/domain/projects/project_orchestrator.py
  6. agent/tg_bot.py — добавить команды

КЛЮЧЕВОЕ (прочтить перед началом):
  - docs/LEVEL6_TZ.md — полное ТЗ
  - claude_manager/providers/pool.py — AllAccountsRateLimited(next_reset_ts),
    pool._earliest_reset_ts(), pool.complete(), pool.migrate_session()
  - claude_manager/logger.py — StepLogger обязателен во всех новых модулях
  - docs/sessions/2026-05-26_claude-manager-level6.md — полный контекст

АРХИТЕКТУРНЫЕ ПРАВИЛА Level 6:
  - ВСЕ модули вызывают LLMProviderPool.complete(), не ClaudeAdapter напрямую
  - ProjectScheduler из ТЗ НЕ делать — избыточный слой, логика в Orchestrator
  - TG-команды: префикс /p... (не путать с /status Gemini-задач):
    /project, /pstatus, /ppause, /presume, /projects, /pqueue, /checkpoints
  - Файлы по архитектуре в claude_manager/domain/projects/, НЕ в core_bridge/
  - Логирование обязательно через StepLogger во всех модулях:
    log.task() → начало (log + TG)
    log.step() → шаг (только log)
    log.result() → итог (log + TG)
    log.error() → ошибка (log + TG-алерт)

NOT сделано (потом):
  - Delivery: дашборд + CLI для Claude Manager — низкий приоритет
```