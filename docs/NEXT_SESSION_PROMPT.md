# Промт для следующей сессии

Скопируй и отправь в начале новой сессии:

---

```
Репозиторий: github.com/lidenal85-blip/Leviathan_Agent (ветка main, коммит 5f8819b)
Сервер: root@78.17.24.96 | leviathanstory.ru

Прочитай docs/sessions/2026-05-25_v3.2-claude-session.md — там полный отчёт предыдущей сессии.

Проект готов к деплою. Задачи по приоритету:

1. ДЕПЛОЙ: выполни команды из раздела "ПРИОРИТЕТ 1: Деплой на сервер" 
   в docs/sessions/2026-05-25_v3.2-claude-session.md.
   GitHub токен нужно заменить на свежий (старый отозван).

2. После деплоя — протестируй: curl http://78.17.24.96:8200/health

3. Если деплой OK — переходи к ПРИОРИТЕТ 2 (React дашборд)
   или ПРИОРИТЕТ 3 (GCE MCP) по желанию.

Стек: Python 3.12 / FastAPI / Gemini 2.0 Flash / aiogram 3 / SQLite
```

---

## Контекст (для понимания без чтения всего репо)

**Что это:** Автономный DevOps-агент. Принимает задачи через Telegram / REST / WebUI,
выполняет их через Gemini function calling (до 50 итераций),
пишет в ExecutionJournal, идемпотентность через OperationRegistry.

**Ключевые файлы:**
- `main.py` — точка входа, инициализация всех зависимостей
- `agent/core.py` — LeviathanAgent (ReAct loop + ModelRouter + ClaudeAdapter)
- `agent/tools.py` + `agent/tools_arbitr.py` — все инструменты
- `config/settings.py` — все параметры из .env
- `mcp_server/leviathan_mcp.py` — MCP для Cursor (stdio) или --http PORT

**Инструменты агента:**
bash_tool, read_file, write_file, list_dir, search_in_files,
git_commit_push, http_get, http_post, claude_think,
arbitr_lisa_estimate, arbitr_pipeline_status, arbitr_pipeline_start,
arbitr_render_prompt, arbitr_submit_response, arbitr_run_auto_stage
