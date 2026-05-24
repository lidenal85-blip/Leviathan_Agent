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

