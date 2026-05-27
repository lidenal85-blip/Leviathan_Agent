# LEVIATHAN AGENT — Правила работы в репозитории
> **Этот файл ОБЯЗАТЕЛЕН к прочтению в начале каждой сессии.**  
> Любой AI-агент (Claude, Gemini, Cursor) начинает с него.

---

## 0. БЫСТРЫЙ СТАРТ СЕССИИ (читай первым)

```bash
# 1. Прочитай состояние проекта
cat docs/AGENT_RULES.md     # этот файл
cat docs/agent_logs.md      # лог всех сессий

# 2. Прочитай последний отчёт сессии
ls docs/sessions/           # найди последний по дате
cat docs/sessions/YYYY-MM-DD_*.md

# 3. Прочитай передаточный промт (если есть)
cat docs/NEXT_SESSION_PROMPT.md

# 4. Только после этого — начинай работу
```

---

## 1. СТРУКТУРА РЕПОЗИТОРИЯ

```
/opt/leviathan_engine/agent_service/      ← РАБОЧАЯ ДИРЕКТОРИЯ
│
├── docs/
│   ├── AGENT_RULES.md          ← ТЫ ЗДЕСЬ. Читай первым.
│   ├── agent_logs.md           ← ГЛАВНЫЙ ЛОГ (все сессии хронологически)
│   ├── NEXT_SESSION_PROMPT.md  ← передаточный промт для следующей сессии
│   ├── sessions/               ← детальные отчёты
│   │   └── YYYY-MM-DD_тема.md
│   ├── LEVEL6_TZ.md            ← ТЗ текущего уровня разработки
│   ├── PROJECT_CONTEXT.md      ← контекст проекта
│   ├── PROMPT_BASE.md          ← база промтов Arbitr-ролей
│   ├── DEPLOY_PRODUCTION.md
│   ├── INTEGRATION_CLAUDE_CODE.md
│   ├── INTEGRATION_CURSOR.md
│   └── SETUP_GCE_MCP.md
│
├── agent/                      ← ядро агента
│   ├── core.py                 ← LeviathanAgent FC-loop — НЕЛЬЗЯ ЛОМАТЬ
│   ├── tg_bot.py               ← Telegram handlers + WebSocket
│   ├── model_router.py         ← роутер Gemini/Groq/Claude ✅
│   ├── gemini_http.py          ← прямые HTTP-вызовы Gemini
│   ├── groq_adapter.py         ← Groq адаптер ✅
│   ├── intent.py               ← Intent Detection
│   ├── tools.py                ← базовые инструменты (bash, file, git...)
│   ├── tools_arbitr.py         ← ArbitrCockpit инструменты
│   ├── tools_adaptive.py       ← адаптивные инструменты (LLMProviderPool) ✅
│   ├── tools_delivery.py       ← delivery инструменты
│   ├── tools_deploy.py         ← deploy инструменты
│   ├── tools_file.py           ← file/send инструменты
│   ├── tools_extra.py          ← дополнительные инструменты
│   ├── tools_diet_platform.py  ← diet platform инструменты
│   └── projects/
│       ├── context.py          ← ProjectContext
│       └── registry.py         ← ProjectRegistry
│
├── claude_manager/             ← Claude multi-account система (feature/claude-multi-account)
│   ├── logger.py               ← StepLogger (обязателен во всех новых модулях)
│   ├── providers/
│   │   ├── pool.py             ← LLMProviderPool — НЕЛЬЗЯ ЛОМАТЬ
│   │   ├── claude/
│   │   │   └── adapter.py      ← ClaudeAdapter ✅
│   │   └── gemini/             ← GeminiProvider (в разработке)
│   ├── core/
│   │   ├── auth/
│   │   │   └── claude_login.py ← Playwright авторизация
│   │   ├── crypto/
│   │   │   └── key_manager.py  ← CryptoKeyManager ✅
│   │   └── storage/
│   │       ├── account_store.py     ← AccountStore ✅
│   │       ├── advisory_lock.py     ← AdvisoryLock ✅
│   │       └── project_store.py     ← ProjectStore ✅
│   └── domain/
│       ├── accounts/
│       │   └── lifecycle_manager.py ← AccountLifecycleManager ✅
│       ├── sessions/
│       │   └── context_manager.py   ← SessionContextManager ✅
│       └── projects/
│           ├── task_planner.py      ← TaskPlanner ✅
│           ├── project_executor.py  ← ProjectExecutor ✅
│           └── [resume/orchestrator — TODO]
│
├── core_bridge/
│   ├── key_pool.py             ← GeminiKeyPool + CircuitBreaker — НЕЛЬЗЯ ЛОМАТЬ
│   └── claude_adapter.py       ← legacy ClaudeAdapter (совместимость)
│
├── db/
│   ├── journal.py              ← ExecutionJournal
│   ├── storage.py              ← TaskStorage
│   ├── context_memory.py       ← ContextMemory (100MB limit) ✅
│   ├── knowledge_base.py       ← KnowledgeBase
│   └── token_stats.py          ← TokenStats
│
├── execution/
│   ├── idempotency.py          ← OperationRegistry
│   ├── result_envelope.py      ← ResultEnvelope
│   └── w3m_syncer.py           ← Termux→DB синхронизатор сессий ✅
│
├── delivery/
│   ├── claude_accounts_web.py  ← веб-интерфейс управления аккаунтами
│   └── templates/
│
├── api/
│   └── openai_compat.py        ← OpenAI-совместимый API
│
├── mcp_server/
│   ├── leviathan_mcp.py        ← MCP сервер (Cursor IDE)
│   └── leviathan_mcp_server.py ← расширенный MCP сервер ✅
│
├── hq/                         ← [TODO] HQ модуль
│
├── config/
│   └── settings.py             ← pydantic-settings (.env) — НЕЛЬЗЯ ЛОМАТЬ
│
├── main.py                     ← FastAPI entry point — НЕЛЬЗЯ ЛОМАТЬ
├── requirements.txt
├── deploy.sh
├── leviathan_agent.service
├── .clinerules                 ← правила для Cline (8000 токенов контекст)
├── .cursor_mcp.json            ← MCP конфиг для Cursor
├── leviathan_audit.py          ← аудит системы
├── get_claude_session.py       ← получение сессий Claude
├── test_claude_adapter.py
└── test_pool.py
```

---

## 2. ПРАВИЛА ЛОГИРОВАНИЯ (ОБЯЗАТЕЛЬНО)

**ШАГ 1 — В начале сессии** → добавить в `docs/agent_logs.md`:
```markdown
## Сессия YYYY-MM-DD — [краткое название]
**Модель:** Claude Sonnet 4.x / Gemini  
**Задача:** [что делаем]  
**Статус:** 🔄 В работе
```

**ШАГ 2 — Каждый значимый шаг:**
```markdown
### ШАГ N — [название] ✅/❌/🔄
- Что сделано
- Файлы: `path/to/file.py`
- Результат / проблемы
```

**ШАГ 3 — В конце сессии** → в `docs/agent_logs.md`:
```markdown
## Итог сессии YYYY-MM-DD
**Статус:** ✅ Завершена / ⚠️ Прервана / 🔄 Продолжается
**Создано:** ...
**Изменено:** ...  
**TODO:** ...
```

**ШАГ 4** — Создать `docs/sessions/YYYY-MM-DD_тема.md`

**ШАГ 5** — Обновить `docs/NEXT_SESSION_PROMPT.md` (передаточный промт)

**ШАГ 6 — Git commit + push:**
```bash
git add -A
git commit -m "тип: описание [session YYYY-MM-DD]"
git push origin feature/claude-multi-account
```

---

## 3. ПРАВИЛА GIT COMMITS

```
тип: описание [session YYYY-MM-DD]
```

| Тип | Когда |
|-----|-------|
| `feat:` | новая функциональность |
| `fix:` | исправление бага |
| `docs:` | документация, логи |
| `arch:` | архитектурные решения |
| `wip:` | незавершённая работа |
| `session:` | итог сессии |

**Правила:**
- Коммить часто — минимум в начале и конце сессии
- Никогда не пушить без обновления `docs/agent_logs.md`
- Если сессия прервалась — коммит с `wip:`
- **Текущая ветка для пуша:** `feature/claude-multi-account`
- В `main` — только стабильные завершённые фичи

---

## 4. КОНТЕКСТ ПРОЕКТА

### Реквизиты
```
Сервер:    root@78.17.24.96
Telegram:  @Levi_Engi_bot
GitHub:    github.com/lidenal85-blip/Leviathan_Agent
Раб. путь: /opt/leviathan_engine/agent_service/
Ветка:     feature/claude-multi-account
```

### Серверная экосистема
```
Port 8200 — Leviathan Agent   /opt/leviathan_engine/agent_service/
Port 8095 — ArbitrCockpit     /opt/arbitr_cockpit
Port 8120 — VoiceStudio       /var/www/voicestudio
Port 8110 — KinoVibe          /var/www/kinovibe
Port 8300 — MCP Server        (внутри agent_service)
```

### LLM стек
```
Primary:    Gemini 2.5-flash (14 ключей, ~8 рабочих)
Secondary:  Groq (4 ключа)
Tertiary:   Claude multi-account (claude_manager)
Routing:    MODEL_MODE=AUTO → LLMProviderPool
```

> ⚠️ Groq — НЕ просто fallback. Это полноценный провайдер в LLMProviderPool.
> Автоматического фоллбека на одну модель нет — pool сам выбирает провайдера.

### Версия и статус
- **v3.2** — feature/claude-multi-account (активная разработка)
- **v3.0** — main (стабильный, устаревший)
- Сервис запущен: `systemctl status leviathan_agent` → `active (running)`
- Сервис работает из `feature/claude-multi-account`

### Что реализовано в feature/claude-multi-account
```
✅ CryptoKeyManager
✅ AccountStore + AdvisoryLock  
✅ AccountLifecycleManager
✅ SessionContextManager
✅ ClaudeAdapter (16/16 тестов)
✅ LLMProviderPool + GroqAdapter
✅ StepLogger
✅ ProjectStore + TaskPlanner + ProjectExecutor
✅ ContextMemory (100MB)
✅ w3m_syncer (Termux → DB)
✅ model_router.py
🔄 resume_manager / project_orchestrator — TODO
🔄 test_pool.py — SyntaxError на стр.242, не починен
```

### Ключевые файлы — НЕЛЬЗЯ ЛОМАТЬ
```
main.py
agent/core.py
config/settings.py
claude_manager/providers/pool.py    ← LLMProviderPool
claude_manager/core/storage/account_store.py
core_bridge/key_pool.py
.env
```

---

## 5. АРХИТЕКТУРНЫЕ ПРАВИЛА (текущий уровень)

1. **Все LLM-вызовы** — только через `LLMProviderPool.complete()`, не напрямую
2. **Логирование** — обязательно через `StepLogger` во всех новых модулях:
   - `log.task()` → начало задачи (log + TG)
   - `log.step()` → шаг (только log)
   - `log.result()` → итог (log + TG)
   - `log.error()` → ошибка (log + TG-алерт)
3. **TG-команды** для projects: префикс `/p...` (не путать с `/status` Gemini-задач)
4. **ProjectScheduler** из ТЗ — НЕ делать, логика в Orchestrator
5. **Новые модули projects** → `claude_manager/domain/projects/`, не в `core_bridge/`

---

## 6. ПРАВИЛА БЕЗОПАСНОСТИ

- Никогда не коммитить `.env`, API ключи, токены
- `.gitignore` содержит `.env` — не обходить
- **GitHub token в URL remote — ОПАСНОСТЬ:** проверить командой:
  ```bash
  git remote -v  # если токен в URL — убрать: git remote set-url origin https://github.com/...
  ```
- При работе с production — режим NORMAL (не FULL)
- Деструктивные операции — запрашивать подтверждение

---

## 7. СТИЛЬ КОДА

- Python 3.11+, async/await везде
- Type hints обязательны для публичных функций
- Docstring на каждый инструмент агента
- Логирование через `logging.getLogger(__name__)` + `StepLogger`
- UTF-8, русский разрешён в комментариях
- `.clinerules`: максимальный контекст 8000 токенов (для Cline)

---

## 8. ЧЕКЛИСТ ПЕРЕД ЗАВЕРШЕНИЕМ СЕССИИ

```
[ ] docs/agent_logs.md обновлён
[ ] docs/sessions/YYYY-MM-DD_*.md создан
[ ] docs/NEXT_SESSION_PROMPT.md обновлён
[ ] ADR созданы для нетривиальных решений
[ ] git add -A && git commit && git push feature/claude-multi-account
```

---

## 9. ЭКСТРЕННОЕ ВОССТАНОВЛЕНИЕ

```bash
# Логи
cat docs/agent_logs.md | tail -100
journalctl -u leviathan_agent -f

# Git история
git log --oneline -20
git stash  # если есть незакоммиченные изменения

# Откат последнего коммита (осторожно)
git revert HEAD

# Health checks
curl http://localhost:8200/health
curl http://localhost:8095/health   # ArbitrCockpit

# Рестарт сервиса
systemctl restart leviathan_agent
systemctl status leviathan_agent
```

---

## CHANGELOG

| Версия | Дата | Изменение |
|--------|------|-----------|
| 1.0 | 2026-05-24 | Первоначальная версия |
| 2.0 | 2026-05-28 | Актуализация: полная структура v3.2, claude_manager, LLMProviderPool, правильные пути/порты, LLM стек, архитектурные правила Level 6 |