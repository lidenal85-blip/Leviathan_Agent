# Передаточный промт — Level 6 (остаток) + Фаза 2

Вставь в начало новой сессии:

---

```
Роль: developer_v2 (ты — Senior Developer, реализуешь по готовым спецификациям)
Репо: github.com/lidenal85-blip/Leviathan_Agent
Ветка: feature/claude-multi-account
Сервер: root@78.17.24.96
Путь: /opt/leviathan_engine/agent_service/

ЧТО УЖЕ СДЕЛАНО (Фаза 1) — НЕ МЕНЯТЬ:
  ✅ TaskStatus.PAUSED в agent/core.py
  ✅ Task: paused_at, pause_reason, current_step, fire_and_forget
  ✅ execution/pipeline_log.py — plog(), PipelineEvent
  ✅ execution/backoff_scheduler.py — BackoffScheduler, _check_api_gate
  ✅ core_bridge/key_pool.py — all_rate_limited()
  ✅ db/storage.py — новые поля, get_paused_tasks()
  ✅ agent/tg_bot.py — hot-resume, fire_and_forget режим

ТЕКУЩАЯ ЗАДАЧА — в порядке приоритета:

1. Фикс test_pool.py SyntaxError стр.242
   - Прочитай test_pool.py, исправь SyntaxError
   - Запусти: cd /opt/leviathan_engine/agent_service && python3 -m py_compile test_pool.py

2. Level 6 остаток:
   - claude_manager/domain/projects/resume_manager.py
   - claude_manager/domain/projects/project_orchestrator.py
   - Спецификация: docs/LEVEL6_TZ.md
   - Интерфейс: claude_manager/providers/pool.py (LLMProviderPool)
   - Правило: все через pool.complete(), не напрямую

3. Фаза 2 (деплой продуктов) — после выполнения пунктов 1-2:
   - Book Factory: LEVIATHAN_refactored (v5) → /opt/book_factory/ :8210
   - Book Downloader → /opt/book_downloader/ :8220
   - Textbook Platform → /opt/textbook_platform/ :8230

АРХИТЕКТУРНЫЕ ПРАВИЛА:
  - ВСЕ LLM — через LLMProviderPool.complete()
  - StepLogger во всех новых модулях
  - Пушить в feature/claude-multi-account
```