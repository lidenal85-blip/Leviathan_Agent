# Итоговый Отчёт: Leviathan Agent — Аналитическая Сессия
**Дата:** 2026-05-24 | **Аналитик:** Claude Sonnet 4.6

---

## 1. АНАЛИЗ LEVIATHAN AGENT

### Архитектура
- **LLM**: Gemini 2.0 Flash, function calling loop, до 50 итераций
- **Transport**: Telegram polling + WebSocket live dashboard
- **Tools**: bash_tool, read_file, write_file, list_dir, search_in_files, git_commit_push, http_get, http_post
- **Safety**: PolicyEngine (DANGEROUS_PATTERNS), 3 режима: SAFE/NORMAL/FULL
- **Persistence**: ExecutionJournal + OperationRegistry (идемпотентность) на SQLite
- **KeyPool**: CircuitBreaker с cooldown при 429, до 14 Gemini ключей + 5 Groq (резерв)

### Сильные стороны
- Полная idempotency через OperationRegistry
- Crash recovery через ExecutionJournal снапшоты
- WebSocket real-time dashboard
- invocation_id на каждый tool call (трассируемость)
- Модальная система подтверждений для опасных операций

### Слабые места
- Привязан только к Gemini (нет Claude/OpenAI fallback)
- Нет интеграции с ArbitrCockpit (хотя llm_adapter.py в Arbitr уже ищет LEVIATHAN Engine)
- SYSTEM_PROMPT не упоминает ArbitrCockpit
- Нет MCP интерфейса для Cursor

---

## 2. АНАЛИЗ ARBITR COCKPIT

### Что это
Веб-кокпит для управления AI-конвейером фриланс-заказов.
State machine: `INTAKE → TRIAGED → QUOTED → ACCEPTED → IN_DEV → TESTING → DELIVERED`

### LISA Formula
```
TC = Σ(axis × weight[project_type]) × k_cal × k_wip × (1 + risk_premium)

Оси: L(Logic) I(Integration) S(State) A(Autonomy) U(Uncertainty) C(Coordination)
Пресеты весов: script|parser|bot_fsm|webapp|api_integration|ai_pipeline
k_cal = калибровка по историческим данным
k_wip = 1 + 0.05 × (active_orders - 1)
risk_premium = сумма флагов риска
```

### Пайплайны
22 роли, 5 типов (bot_fsm/webapp/parser/api_integration/script).
Полный bot_fsm: 17 стадий включая optional.

### Связь с Leviathan
`llm_adapter.py` уже содержит: `from core.llm_factory import LLMFactory`
Arbitr изначально проектировался как клиент LEVIATHAN Engine.
Интеграция заложена в архитектуре — нужно только замкнуть связь.

---

## 3. АНАЛИЗ ПРОМТОВ

### Decomposer v1 → v2
**Эволюция**: от простого "разбей на модули" к полноценному системному декомпозитору.
**v2 добавляет**: bounded contexts, module types классификация (Domain/Integration/Infrastructure...), self-validation checklist, dependency rules (no cyclic, no shared mutable), development sequencing (5 фаз).

### Architect v1 → v2  
**Эволюция**: от "выдай схему" к Senior/Staff Engineer уровню.
**v2 добавляет**: ADR-формат (Architecture Decision Records), non-functional requirements раздел, failure propagation model, evolution path (MVP→Growth→Production), hard rules против overengineering.

### Auditor v2
**Роль**: production-ориентированный ревизор, не архитектор.
**Severity model**: Critical/High/Medium/Low.
**Обязательные зоны**: Domain Integrity, Data Flow, Integration Safety, Failure Scenarios, Security, Observability.
**Вердикт**: READY / READY WITH FIXES / NOT READY.

---

## 4. СОЗДАННЫЕ АРТЕФАКТЫ

### agent/tools_arbitr.py
6 инструментов для интеграции с ArbitrCockpit:
- `arbitr_lisa_estimate` — автономный LISA расчёт (без сети, вшиты формулы)
- `arbitr_pipeline_status` — статус конвейера заказа
- `arbitr_pipeline_start` — запустить стадию (mode=auto→LLM сразу)
- `arbitr_render_prompt` — получить рендеренный промт стадии
- `arbitr_submit_response` — отправить ответ в стадию
- `arbitr_run_auto_stage` — автоматический LLM-вызов

Все инструменты снабжены Gemini function declarations для FC-loop.

### mcp_server/leviathan_mcp.py
MCP server (stdio, JSON-RPC 2.0) для подключения к Cursor IDE.
5 инструментов: leviathan_task, leviathan_status, arbitr_lisa, arbitr_pipeline_status, arbitr_pipeline_advance.

### docs/INTEGRATION_CLAUDE_CODE.md
План реализации `core_bridge/claude_adapter.py`:
- Вызов `claude --print "prompt" --output-format json` через subprocess
- Fallback в KeyPool когда все Gemini ключи исчерпаны
- Добавление `USE_CLAUDE_FALLBACK: bool` в Settings

### docs/PROMPT_BASE.md
- Анализ всех промтов с ключевыми принципами
- Расширенный SYSTEM_PROMPT для агента v3.1
- Промты для ролей: Decomposer / Architect / Auditor
- Схема цепочки вызовов для типового заказа

---

## 5. ПЛАН ИНТЕГРАЦИИ

### Claude Code (3 шага)
1. Создать `core_bridge/claude_adapter.py` (план готов)
2. Добавить `get_key_or_claude()` в KeyPool
3. Обработать provider="claude" в `agent/core.py`

### Cursor IDE (2 способа)
- **Быстро**: скопировать `.cursor_mcp.json` → запустить mcp_server
- **Правильно**: добавить `~/.cursor/mcp.json` глобально + `.cursorrules`

### ArbitrCockpit (1 строка)
```python
# В agent/tools.py (конец файла):
from agent.tools_arbitr import register_arbitr_tools
register_arbitr_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
```

---

## 6. РЕКОМЕНДАЦИИ

1. **Сразу**: добавить arbitr tools в tools.py — одна строка, нулевой риск
2. **Приоритет**: Claude Code fallback — снижает зависимость от Gemini quota
3. **MCP для Cursor** — увеличивает скорость работы с сервером из IDE
4. **SYSTEM_PROMPT** — добавить ArbitrCockpit + роли Decomposer/Architect/Auditor
5. **Долгосрочно**: LEVIATHAN Engine как общий LLM factory для Arbitr + Leviathan Agent

