# Промт для следующей сессии

## Контекст проекта

Leviathan Agent v3.0 — автономный DevOps-агент на Gemini function calling.
Репозиторий: `github.com/lidenal85-blip/Leviathan_Agent`
Сервер: `leviathanstory.ru`

**Что уже сделано в предыдущей сессии (2026-05-24):**

1. Проанализированы все файлы агента: core.py, tools.py, settings, key_pool
2. Проанализирован ArbitrCockpit v0.5: LISA, pipeline_engine, blueprints
3. Проанализированы промты: decomposer v1/v2, architect v1/v2, auditor v2
4. Созданы файлы:
   - `agent/tools_arbitr.py` — 6 Arbitr инструментов для агента
   - `mcp_server/leviathan_mcp.py` — MCP server для Cursor
   - `.cursor_mcp.json` — конфиг Cursor MCP
   - `docs/INTEGRATION_CLAUDE_CODE.md` — план интеграции с Claude Code
   - `docs/INTEGRATION_CURSOR.md` — план интеграции с Cursor
   - `docs/PROMPT_BASE.md` — база промтов (анализ + расширенный SYSTEM_PROMPT)

---

## Задачи для следующей сессии

### ПРИОРИТЕТ 1: Активация Arbitr инструментов в агенте

В файле `agent/tools.py` добавить в конец:
```python
# Подключаем ArbitrCockpit инструменты
try:
    from agent.tools_arbitr import register_arbitr_tools
    register_arbitr_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
except ImportError:
    pass  # ArbitrCockpit не установлен — работаем без него
```

Добавить в `.env`:
```
ARBITR_URL=http://localhost:8090
```

### ПРИОРИТЕТ 2: Claude Code Adapter

Реализовать `core_bridge/claude_adapter.py` по плану из `docs/INTEGRATION_CLAUDE_CODE.md`.
Подключить как fallback в `core_bridge/key_pool.py`.

### ПРИОРИТЕТ 3: Расширенный SYSTEM_PROMPT

Заменить SYSTEM_PROMPT в `agent/core.py` на расширенный из `docs/PROMPT_BASE.md` (раздел 4).
Добавить упоминание ArbitrCockpit (port 8090) и arbitr инструментов.

### ПРИОРИТЕТ 4: MCP сервер в production

```bash
# На сервере:
pip install httpx  # если не установлен
cp /opt/leviathan_agent/.cursor_mcp.json ~/.cursor/mcp.json
# Или в проект Cursor:
cp .cursor_mcp.json /path/to/project/.cursor/mcp.json
# Тест:
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 /opt/leviathan_agent/mcp_server/leviathan_mcp.py
```

### ПРИОРИТЕТ 5: Промты ролей в system prompt

Агент должен уметь переключаться в роль Decomposer/Architect/Auditor через task prompt.
Добавить в SYSTEM_PROMPT секцию:
```
═══ РЕЖИМЫ РОЛИ ═══
Если задача начинается с "[DECOMPOSER]" — действуй как системный декомпозитор
Если задача начинается с "[ARCHITECT]" — действуй как архитектор
Если задача начинается с "[AUDITOR]" — действуй как аудитор архитектуры
```

---

## Состояние сервера (для контекста)

```
Порты:
  8200 — Leviathan Agent (FastAPI + WS + Telegram)
  8090 — ArbitrCockpit (pipeline cockpit)
  8005 — Orionyx (инвестиционная платформа)
  8000 — AI Outreach
  8120 — VoiceStudio
  8110 — KinoVibe

Пути:
  /opt/leviathan_agent   — этот агент
  /opt/arbitr_cockpit    — Arbitr Cockpit
  /opt/orionyx           — Orionyx
  /opt/leviathan_engine  — LEVIATHAN Engine (core/llm_factory.py)
  /var/www/voicestudio
  /var/www/kinovibe

DB: SQLite (db/leviathan.db)
LLM: Gemini 2.0 Flash (до 14 ключей)
```

---

## Ключевые команды для быстрого старта

```bash
# Статус агента
curl http://localhost:8200/health

# Тест Arbitr tools (после активации)
curl -X POST http://localhost:8200/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Оцени заказ: бот для записи клиентов в Telegram с оплатой через ЮКасса. Используй arbitr_lisa_estimate.", "mode":"NORMAL"}'

# Тест MCP
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  python3 /opt/leviathan_agent/mcp_server/leviathan_mcp.py

# Логи агента
journalctl -u leviathan-agent -n 50 -f
```

