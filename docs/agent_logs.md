# LEVIATHAN AGENT — Лог Аналитической Сессии
**Дата:** 2026-05-24  
**Сессия:** Claude Sonnet 4.6  
**Задача:** Анализ агента, план интеграций, база промтов, Arbitr Cockpit Tool

---

## ВЫПОЛНЕННЫЕ ШАГИ

### ШАГ 1 — Клонирование и изучение репозитория ✅
**Репозиторий:** `github.com/lidenal85-blip/Leviathan_Agent`  
**Структура v3.0:**
- `agent/core.py` — LeviathanAgent: Gemini function calling loop, до 50 итераций
- `agent/tools.py` — 8 инструментов: bash, read/write_file, list_dir, search, git, http
- `agent/tg_bot.py` — Telegram polling + WebSocket live log
- `config/settings.py` — pydantic-settings, 14 Gemini + 5 Groq ключей
- `core_bridge/key_pool.py` — GeminiKeyPool + CircuitBreaker
- `db/journal.py` — ExecutionJournal (запись каждого шага)
- `execution/idempotency.py` — OperationRegistry (дедупликация)
- `main.py` — FastAPI + WebSocket dashboard (порт 8200)

**Ключевые находки:**
- Агент работает ТОЛЬКО на Gemini (google-generativeai)
- Нет нативной интеграции с Claude / Anthropic
- Системный промт знает о 5 проектах на сервере + GitHub
- 3 режима: SAFE / NORMAL / FULL
- PolicyEngine для опасных команд (rm -rf, DROP TABLE, systemctl stop)

### ШАГ 2 — Анализ Arbitr Cockpit v0.5 ✅
**Ключевые компоненты:**
- `pipeline_engine.py` — state machine, system.yaml, stage transitions
- `lisa_calculator.py` — LISA formula: TC = Σ(axis×weight) × k_cal × k_wip × (1+risk)
- `llm_adapter.py` — уже ищет LEVIATHAN Engine symlink (`core/llm_factory.py`)!
- `prompt_renderer.py` — Jinja2 рендер блюпринтов с контекстом заказа
- `system.yaml` — 22 роли, 5 типов пайплайнов

**LISA формула:**
```
TC = (L×wL + I×wI + S×wS + A×wA + U×wU + C×wC) × k_cal × k_wip × (1+risk_premium)
Уровни: Junior(≤4) → Mid(≤6) → Senior(≤8) → Expert(≤9) → Architect(≤10)
```

**Пайплайн bot_fsm (15 стадий):**
```
triage → risk_manager → lisa_estimator → explainer → response_writer 
→ negotiator* → decomposer → survey_normalizer* → architect → arch_auditor 
→ session_planner → developer → fsm_test_planner* → tester → fixer* → documenter → post_mortem
```

### ШАГ 3 — Анализ промтов ✅
**Прочитано:** architect.md, architect_v2.md, decomposer.md, decomposer_v2.md, architect_auditor_v2.md

**Иерархия ролей:**
```
DECOMPOSER → ARCHITECT → AUDITOR
```

**Ключевые принципы:**
- Decomposer: bounded contexts, explicit contracts, data ownership, low coupling
- Architect: ADR-формат решений, no code, failure modes, evolution path
- Auditor: ревизор не архитектор, severity model, production readiness verdict

### ШАГ 4 — Разработка артефактов ✅

| Файл | Назначение |
|------|-----------|
| `docs/INTEGRATION_CLAUDE_CODE.md` | Plan: ClaudeCodeAdapter + fallback в KeyPool |
| `docs/INTEGRATION_CURSOR.md` | Plan: MCP server + .cursorrules + REST |
| `docs/PROMPT_BASE.md` | База промтов: анализ + расширенный SYSTEM_PROMPT |
| `agent/tools_arbitr.py` | 6 инструментов Arbitr для агента + Gemini declarations |
| `mcp_server/leviathan_mcp.py` | MCP server для Cursor (stdio, JSON-RPC 2.0) |
| `.cursor_mcp.json` | Конфиг для Cursor MCP |

---

## АРХИТЕКТУРА ИТОГОВОЙ СИСТЕМЫ

```
┌──────────────────────────────────────────────────────────────────┐
│                        CURSOR IDE                                │
│  .cursor/mcp.json → leviathan_mcp.py (stdio)                    │
│  .cursorrules → описание API для AI-assistant                    │
└──────────────┬───────────────────────────────────────────────────┘
               │ MCP / REST
               ▼
┌──────────────────────────────────────────────────────────────────┐
│              LEVIATHAN AGENT v3.1 (port 8200)                    │
│                                                                  │
│  LeviathanAgent (core.py)                                        │
│  ├── GeminiKeyPool (основной LLM)                                │
│  └── ClaudeCodeAdapter (fallback, если все Gemini на cooldown)   │
│                                                                  │
│  Tools:                                                          │
│  ├── bash/file/git/http (DevOps)                                 │
│  └── arbitr_lisa/pipeline/* (Arbitr Cockpit)                     │
│                                                                  │
│  Roles (через task prompt):                                      │
│  Decomposer / Architect / Auditor / Developer / Tester           │
└─────────┬──────────────────────────────────────────┬────────────┘
          │ bash/ssh                                  │ HTTP API
          ▼                                           ▼
   Linux Server                              ArbitrCockpit (port 8090)
   (5 проектов)                              Pipeline State Machine
```

---

## СТАТУС ЗАДАЧ

| Задача | Статус |
|--------|--------|
| Анализ агента | ✅ |
| Анализ Arbitr Cockpit | ✅ |
| Анализ промтов | ✅ |
| Plan: Claude Code integration | ✅ |
| Plan: Cursor integration | ✅ |
| tools_arbitr.py | ✅ |
| mcp_server/leviathan_mcp.py | ✅ |
| Prompt Base | ✅ |
| Итоговый отчёт + промт | ✅ (ниже) |


---

## Сессия 2026-05-24 — Интеграция v3.1: ModelRouter + ClaudeAdapter + ArbitrTools
**Модель:** Claude Sonnet 4.6  
**Задача:** Активация приоритетов из NEXT_SESSION_PROMPT.md  
**Статус:** ✅ Завершена

### ШАГ 1 — Изучение репо и восстановление файлов ✅
- Обнаружили что репо содержало v3.0 с ArbitrCockpit, MCP, docs
- Восстановили 26 файлов через GitHub API после случайного force push
- Файлы: docs/, mcp_server/, execution/, core_bridge/, agent/tools_arbitr.py

### ШАГ 2 — Запуш новых файлов от Denis ✅
- `agent/model_router.py` — роутер Gemini/Claude (5 режимов: AUTO/GEMINI_ONLY/CLAUDE_ONLY/GEMINI_THINK_CLAUDE/CLAUDE_THINK_GEMINI)
- `core_bridge/claude_adapter.py` — CLI+API адаптер, extended thinking
- `main.py` v3.1 — NullNotifier, ModelRouter интеграция, дашборд с выбором модели

### ШАГ 3 — ПРИОРИТЕТ 1: Активация tools_arbitr.py ✅
- `agent/tools.py`: добавлен `register_arbitr_tools()` в конце файла
- Импорт через try/except — если ArbitrCockpit не установлен, работает без него

### ШАГ 4 — Settings + .env.example обновлены ✅
- `config/settings.py`: добавлены ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_THINKING_BUDGET, MODEL_MODE, ARBITR_URL
- `.env.example`: задокументированы все новые переменные

### ШАГ 5 — ПРИОРИТЕТ 3: Расширенный SYSTEM_PROMPT ✅
- `agent/core.py`: SYSTEM_PROMPT заменён на v3.1 из docs/PROMPT_BASE.md
- Добавлено: ArbitrCockpit, [DECOMPOSER]/[ARCHITECT]/[AUDITOR] режимы ролей
- Добавлено: Claude CLI fallback инструкция

## Итог сессии 2026-05-24
**Статус:** ✅ Завершена  
**Создано:** docs/sessions/2026-05-24_v3.1-integration.md  
**Изменено:** agent/tools.py, agent/core.py, config/settings.py, .env.example, main.py, agent/model_router.py, core_bridge/claude_adapter.py  
**TODO:** ПРИОРИТЕТ 2 (claude_adapter.py в KeyPool fallback), ПРИОРИТЕТ 4 (MCP в production), деплой на сервер


### Продолжение сессии 2026-05-24 (18:47 UTC)

**ПРИОРИТЕТ 2 — Claude fallback в KeyPool ✅**
- `agent/core.py`: добавлены импорты ClaudeAdapter (try/except)
- `LeviathanAgent.__init__`: параметр `claude_adapter`, авто-инициализация `self._claude`
- Fallback логика: если Gemini → Exception (не 429) → `_run_claude_fallback()`
- Новый метод `_run_claude_fallback()`: конвертирует историю в промт → вызывает Claude API/CLI

**ПРИОРИТЕТ 4 — MCP в production ✅**
- MCP сервер был готов (`mcp_server/leviathan_mcp.py`) — stdio JSON-RPC 2.0
- `docs/DEPLOY_PRODUCTION.md`: полная инструкция деплоя на сервер 78.17.24.96
  - .env шаблон с реальными токенами
  - nginx конфиг для /agent/ и /agent/ws (WebSocket)
  - MCP конфиг для Cursor IDE
  - Команды проверки


### Обновление сессии 2026-05-24 (18:49) — Denis загрузил финальные файлы v3.1

| Файл | Путь | Изменения |
|------|------|-----------|
| core.py | agent/core.py | v3.1 ModelRouter/ClaudeAdapter полная интеграция, gemini-2.5-flash, per-task model_mode |
| settings.py | config/settings.py | GEMINI_K1-K14 (14 ключей индивидуально) |
| tools.py | agent/tools.py | финальная версия с ArbitrCockpit внутри |
| tg_bot.py | agent/tg_bot.py | улучшенный интерфейс |
| leviathan_audit.py | leviathan_audit.py | **НОВЫЙ** — скрипт полного аудита и патча репо через GitHub API |
| README.md | README.md | полная документация 11KB |
| .env.example | .env.example | новая схема GEMINI_K1-K14 |


### Продолжение сессии 2026-05-24 (18:56) — Анализ tar + фиксы main.py

**Анализ Leviathan_Agent_tar.gz (локальное состояние Denis):**
- Git: коммит `7450626` — отстаёт от GitHub на 1 коммит (нет leviathan_audit.py)
- DB: `db/leviathan.db` 72KB — агент реально запускался локально
- `.env`: 12 Gemini ключей (K1-K6, K9-K14), ANTHROPIC_API_KEY, TG_BOT_TOKEN заполнены
- Найден дублирующийся `agent/key_pool.py` — нигде не импортируется

**Исправления:**

1. `main.py` — подключены model_router и claude_adapter в LeviathanAgent:
   - Добавлен импорт `ClaudeAdapter` (try/except)
   - Инициализация `_claude_adapter` из `ANTHROPIC_API_KEY` + `CLAUDE_MODEL`
   - `LeviathanAgent(model_router=model_router, claude_adapter=_claude_adapter)`

2. `agent/key_pool.py` — удалён как дублирующий `core_bridge/key_pool.py`
   - Везде используется `core_bridge/key_pool.py` (с engine fallback)
   - Нигде не импортировался — безопасно удалить

**Для Denis: синхронизация сервера:**
```bash
cd /opt/leviathan_agent  # или где установлен
git pull origin main
systemctl restart leviathan_agent
curl http://localhost:8200/health
```

---

## Сессия 2026-05-26 — Диагностика + claude-multi-account cleanup
**Модель:** Claude Sonnet 4.6 (claude.ai MCP)
**Задача:** Диагностика Gemini 2.5-flash, аудит ветки feature/claude-multi-account, cleanup и продолжение разработки
**Статус:** 🔄 В работе

### ШАГ 1 — Диагностика Gemini ✅
- Проверены все 14 ключей на gemini-2.5-flash напрямую через API
- Результат: K1,K3-K6,K11-K13 → OK (8 рабочих)
- K7,K8 → 403 PERMISSION_DENIED (заблокированы на уровне GCP проекта, не quota)
- K2,K9,K10,K14 → 503 временная недоступность
- GEMINI_MODEL уже был gemini-2.5-flash в settings.py — модель правильная

### ШАГ 2 — Фикс FC-loop ("Unknown field for Schema: default") ✅
- Найдена причина: tools_delivery.py строка 207 содержала "default": 5 в Gemini Schema
- Gemini 2.5-flash не принимает поле default в function declarations
- Фикс: убрал "default": 5, перенёс значение в description
- Перезапустил агент, проверил — FC-loop работает, bash_tool вызвался через provider=gemini
- Файл: agent/tools_delivery.py

### ШАГ 3 — Аудит ветки feature/claude-multi-account ✅
- 6 коммитов поверх main, 2537 строк
- Написано и работает:
  * CryptoKeyManager (58 строк, тесты OK)
  * AccountStore (172 строки, тесты OK)
  * AdvisoryLock (109 строк, тесты OK)
  * AccountLifecycleManager (488 строк, 7/7 тестов OK)
  * SessionContextManager (228 строк)
  * ClaudeAdapter (511 строк, 16/16 тестов OK)
  * LLMProviderPool (397 строк)
  * StepLogger (113 строк)
- Незакоммичено: test_pool.py (SyntaxError стр.242), pool.py, groq_adapter.py,
  tools_adaptive.py, docs/LEVEL6_TZ.md, mcp_server/leviathan_mcp_server.py
- Мусор: lifecycle_manager.py.bak, test_lifecycle.py.bak, settings.py.bak
- Следующий уровень: LEVEL6_TZ.md описывает TaskPlanner + ProjectExecutor

### ШАГ 4 — Cleanup + фикс test_pool.py 🔄

---

## Сессия 2026-05-28 — Архитектура экосистемы + документация
**Модель:** Claude Sonnet 4.6 (claude.ai MCP)  
**Задача:** Аудит документации, анализ 3 продуктов, закладка архитектуры экосистемы  
**Статус:** ✅ Завершена

### ШАГ 1 — Обновление AGENT_RULES.md ✅
- Полная актуализация: структура v3.2, claude_manager, LLM-стек, порты
- Коммит: da474d7

### ШАГ 2 — Удаление /opt/leviathan_agent/ ✅
- Пустая директория с заглушкой .env — удалена
- Активный агент: /opt/leviathan_engine/agent_service/

### ШАГ 3 — Анализ 3 продуктов (архивы) ✅
- LEVIATHAN_refactored (v5): 27 агентов, блоки 1-4 ✅, блок 5 WIP
- book_downloader: структура есть, sources/ не дописаны
- textbook_platform: MVP, сервисы не завершены

### ШАГ 4 — Документация экосистемы ✅
- docs/ECOSYSTEM.md — карта продуктов, порты, петля замыкания
- docs/INTEGRATION_PROTOCOL.md — HTTP-контракт, health/metrics/POST форматы
- docs/ROADMAP.md — 5 фаз, чеклисты

## Итог сессии 2026-05-28
**Статус:** ✅ Завершена  
**Создано:** ECOSYSTEM.md, INTEGRATION_PROTOCOL.md, ROADMAP.md  
**Изменено:** AGENT_RULES.md (v2.0), удалён /opt/leviathan_agent/  
**Следующее:** Фаза 1 — PAUSED state + 429-backoff + pipeline.log
