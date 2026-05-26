# Сессия 2026-05-26 — Claude Manager + Level 6 TZ

**Модель:** Claude Sonnet 4.6 (claude.ai MCP)  
**Ветка:** `feature/claude-multi-account`  
**Последний коммит:** `f932ca0`  
**Статус:** ✅ Завершена

---

## Что сделано в эту сессию

### Диагностика + фиксы
- Gemini 2.5-flash ключи: K1,K3-K6,K11-K13 работают, K7/K8 — 403 GCP, K2/K9/K10/K14 — 503
- Фикс FC-loop: `tools_delivery.py` стр.207 — убран `"default": 5` из Gemini Schema
- Агент работает: bash_tool вызывается через Gemini FC

### Claude Manager (всё в `claude_manager/`)

| Модуль | Файл | Коммит |
|---|---|---|
| StepLogger | `logger.py` | `40736f4` |
| CryptoKeyManager | `core/crypto/key_manager.py` | `a5880ed` |
| AccountStore | `core/storage/account_store.py` | `a5880ed` |
| AdvisoryLock | `core/storage/advisory_lock.py` | `39a0503` |
| AccountLifecycleManager | `domain/accounts/lifecycle_manager.py` | `39a0503` |
| SessionContextManager | `domain/sessions/context_manager.py` | `fffe8e6` |
| ClaudeAdapter | `providers/claude/adapter.py` | `e977e51` |
| LLMProviderPool | `providers/pool.py` | `f932ca0` |

### Дополнительно закоммичено
- `agent/groq_adapter.py` — Groq FC-loop (llama-3.3-70b)
- `agent/tools_adaptive.py` — pip_install, web_search, send_telegram_file
- `mcp_server/leviathan_mcp_server.py` — FastMCP v1.0 порт 8300
- `test_pool.py` — 8/8 тестов GREEN
- `docs/LEVEL6_TZ.md` — полное ТЗ Level 6

---

## Состояние на момент остановки

### Что ещё НЕ сделано

**Delivery (Multi-Account Manager часть 3 ТЗ):**
```
claude_manager/delivery/web/    — дашборд (поллинг)
    dashboard.py                — /dashboard/claude, /dashboard/claude/add
claude_manager/delivery/cli/    — cli.py (--ask, --claude-status, --claude-add)
```

**TG-команды Claude Manager:**
```
/claude <текст>   — запрос через Claude
/claude_status    — статус всех аккаунтов
/claude_rotate    — сменить аккаунт
/claude_add       — добавить аккаунт
```

**Level 6 (не начат):**
```
claude_manager/core/storage/project_store.py      — таблицы projects + project_steps
claude_manager/domain/projects/task_planner.py    — разбивка на шаги через pool
claude_manager/domain/projects/project_executor.py— цикл шагов
  claude_manager/domain/projects/resume_manager.py    — asyncio.sleep на earliest_reset
claude_manager/domain/projects/project_orchestrator.py — очередь
agent/tg_bot.py — добавить /project, /pstatus, /pause, /resume, /projects,
                                              /queue, /checkpoints
```

---

## Приоритеты следующей сессии

1. 🔴 **Level 6: project_store.py** — старт с нёго
2. 🔴 **Level 6: task_planner.py** — промпт к pool, парсинг JSON шагов
3. 🔴 **Level 6: project_executor.py** — петля AllAccountsRateLimited
4. 🔴 **Level 6: resume_manager.py** — asyncio.sleep(расчёт earliest_reset)
5. 🔴 **Level 6: project_orchestrator.py** — очередь + диспатч
6. 🟡 **Delivery: дашборд + CLI + TG-команды** (Claude manager)

---

## Ключевые детали архитектуры

```python
# AllAccountsRateLimited уже есть в pool.py стр.53
# next_reset_ts = float (unix timestamp)
# pool._earliest_reset_ts() возвращает min() по всем аккаунтам

# Фон в ResumeManager:
async def _wait_and_resume(project_id, wait_seconds):
    await asyncio.sleep(wait_seconds)
    # pool.complete() сам выберет свободный аккаунт
    await project_executor.execute_next_step(project_id)

# ProjectOrchestrator: не делать ProjectScheduler — избыточный слой

# TG-команды Level 6: префикс /p... чтобы не путать с /status Gemini-задач
# /project, /pstatus, /ppause, /presume, /projects, /pqueue, /checkpoints
```

---

## Быстрый старт следующей сессии

```
Роль: developer_v2 (Senior Developer)
Ветка: feature/claude-multi-account
Последний коммит: f932ca0

Текущая задача: Level 6 — ProjectStore
Файл: claude_manager/core/storage/project_store.py

Зависимости уже готовы:
- LLMProviderPool (в pool.py) с AllAccountsRateLimited(next_reset_ts)
- AccountLifecycleManager.get_active_accounts() + rate_limit_reset_ts
- SessionContextManager — сессии и история
- StepLogger — логирование во всех модулях

Прочитай перед началом:
- docs/sessions/2026-05-26_claude-manager-level6.md (этот файл)
- docs/LEVEL6_TZ.md
- claude_manager/providers/pool.py (AllAccountsRateLimited + _earliest_reset_ts)
- claude_manager/logger.py (StepLogger)
```