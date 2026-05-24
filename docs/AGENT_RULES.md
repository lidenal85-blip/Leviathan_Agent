# LEVIATHAN AGENT — Правила работы в репозитории
> **Этот файл ОБЯЗАТЕЛЕН к прочтению в начале каждой сессии.**  
> Любой AI-агент (Claude, Gemini, Cursor) начинает с него.

---

## 0. БЫСТРЫЙ СТАРТ СЕССИИ (читай первым)

```bash
# 1. Прочитай состояние проекта
cat AGENT_RULES.md          # этот файл
cat PROJECT_STATE.md        # текущее состояние + что в работе
cat docs/agent_logs.md      # лог всех сессий

# 2. Прочитай последний отчёт сессии
ls docs/sessions/           # найди последний по дате
cat docs/sessions/YYYY-MM-DD_*.md

# 3. Только после этого — начинай работу
```

---

## 1. СТРУКТУРА РЕПОЗИТОРИЯ

```
Leviathan_Agent/
│
├── AGENT_RULES.md          ← ТЫ ЗДЕСЬ. Читай первым.
├── PROJECT_STATE.md        ← Текущий статус, что делается, что ждёт
│
├── agent/
│   ├── core.py             ← LeviathanAgent (Gemini FC-loop)
│   ├── tools.py            ← 8 базовых инструментов
│   ├── tools_arbitr.py     ← 6 ArbitrCockpit инструментов
│   ├── tg_bot.py           ← Telegram + WebSocket
│   └── model_router.py     ← [TODO] роутер Gemini/Claude
│
├── core_bridge/
│   ├── key_pool.py         ← GeminiKeyPool + CircuitBreaker
│   └── claude_adapter.py   ← [TODO] Claude CLI адаптер
│
├── mcp_server/
│   └── leviathan_mcp.py   ← MCP сервер для Cursor IDE
│
├── docs/
│   ├── agent_logs.md       ← ГЛАВНЫЙ ЛОГ (все сессии хронологически)
│   ├── sessions/           ← Детальные отчёты по каждой сессии
│   │   └── YYYY-MM-DD_тема.md
│   ├── decisions/          ← ADR — Architecture Decision Records
│   │   └── ADR-001_название.md
│   └── architecture/       ← Схемы, диаграммы, описания
│
├── config/
│   └── settings.py         ← pydantic-settings (читает .env)
├── main.py                 ← FastAPI app (порт 8200)
└── requirements.txt
```

---

## 2. ПРАВИЛА ЛОГИРОВАНИЯ (ОБЯЗАТЕЛЬНО)

### Каждая сессия ДОЛЖНА:

**ШАГ 1 — В начале сессии:**
```markdown
# В docs/agent_logs.md добавить:
## Сессия YYYY-MM-DD — [краткое название]
**Модель:** Claude Sonnet 4.x / Gemini  
**Задача:** [что делаем]  
**Статус:** 🔄 В работе
```

**ШАГ 2 — Каждый значимый шаг:**
```markdown
### ШАГ N — [название] ✅/❌/🔄
- Что сделано
- Файлы изменены: `path/to/file.py`
- Результат / проблемы
```

**ШАГ 3 — В конце сессии:**
```markdown
## Итог сессии YYYY-MM-DD
**Статус:** ✅ Завершена / ⚠️ Прервана / 🔄 Продолжается
**Создано:** список файлов
**Изменено:** список файлов  
**TODO:** что осталось
```

**ШАГ 4 — Создать файл сессии:**
```
docs/sessions/YYYY-MM-DD_краткое-название.md
```

**ШАГ 5 — Обновить PROJECT_STATE.md**

**ШАГ 6 — Git commit + push:**
```bash
git add -A
git commit -m "тип: краткое описание [session YYYY-MM-DD]"
git push origin main
```

---

## 3. ПРАВИЛА GIT COMMITS

### Формат сообщения:
```
тип: описание [session YYYY-MM-DD]

- что конкретно сделано
- файлы изменены
```

### Типы:
| Тип | Когда |
|-----|-------|
| `feat:` | новая функциональность |
| `fix:` | исправление бага |
| `docs:` | документация, логи |
| `arch:` | архитектурные решения |
| `wip:` | незавершённая работа (промежуточный коммит) |
| `session:` | итог сессии |

### Правила:
- **Коммить часто** — минимум в начале и конце сессии
- **Коммить при каждом значимом шаге** — не накапливай
- **Никогда не пушить без docs/agent_logs.md обновления**
- **Если сессия прервалась** — коммитить с `wip:` и пометкой в логе

---

## 4. ПРАВИЛА ОБНОВЛЕНИЯ PROJECT_STATE.md

После каждой сессии обновлять секции:
- `## Текущий статус` — что работает, что нет
- `## В работе` — активные задачи
- `## TODO` — приоритетная очередь
- `## Известные проблемы` — баги, ограничения

---

## 5. ПРАВИЛА ARCHITECTURE DECISIONS

Каждое **нетривиальное решение** → ADR файл:

```
docs/decisions/ADR-NNN_название.md
```

Шаблон ADR:
```markdown
# ADR-NNN: Название решения
**Дата:** YYYY-MM-DD  
**Статус:** Proposed / Accepted / Deprecated  
**Сессия:** ссылка на docs/sessions/

## Контекст
Какая проблема решается.

## Решение
Что выбрано и почему.

## Альтернативы
Что рассматривалось.

## Trade-offs
Что выигрываем, что теряем.

## Последствия
Какие ограничения создаёт.
```

---

## 6. КОНТЕКСТ ПРОЕКТА (для новых сессий)

### Что такое Leviathan Agent
Автономный DevOps + Arbitr агент. Работает на сервере `leviathanstory.ru`.
- **Версия:** v3.0 (FC-loop Gemini) → v3.1 (multi-model, в разработке)
- **LLM:** Gemini 2.0 Flash (основной) + Claude (планируется)
- **Интерфейсы:** FastAPI REST, WebSocket dashboard, Telegram bot

### Серверная экосистема
```
Port 8200 — Leviathan Agent (этот проект)
Port 8090 — ArbitrCockpit (pipeline cockpit для фриланс-заказов)  
Port 8005 — Orionyx (инвестиционная платформа)
Port 8000 — AI Outreach
Port 8120 — VoiceStudio
Port 8110 — KinoVibe

Пути:
/opt/leviathan_agent    ← этот проект
/opt/arbitr_cockpit     ← ArbitrCockpit
/opt/orionyx            ← Orionyx (Максим активно разрабатывает)
/opt/leviathan_engine   ← LEVIATHAN Engine (core/llm_factory.py)
/var/www/voicestudio
/var/www/kinovibe
```

### Ключевые файлы которые НЕЛЬЗЯ сломать
- `agent/core.py` — ядро агента, FC-loop
- `config/settings.py` — конфигурация
- `main.py` — FastAPI entry point
- `.env` — секреты (не в репо!)

### Что в работе прямо сейчас
→ Смотри `PROJECT_STATE.md`

---

## 7. ПРАВИЛА БЕЗОПАСНОСТИ

- **Никогда** не коммитить `.env`, API ключи, токены
- `.gitignore` уже содержит `.env` — не обходить
- GitHub token в этом файле **не хранить**
- При работе с production сервером — режим NORMAL (не FULL)

---

## 8. СТИЛЬ КОДА

- Python 3.11+, async/await везде
- Type hints обязательны для публичных функций
- Docstring на каждый инструмент агента
- Логирование через `logging.getLogger(__name__)`
- Все строки — UTF-8, русский язык разрешён в комментариях

---

## 9. ЧЕКЛИСТ ПЕРЕД ЗАВЕРШЕНИЕМ СЕССИИ

```
[ ] docs/agent_logs.md обновлён
[ ] docs/sessions/YYYY-MM-DD_*.md создан
[ ] PROJECT_STATE.md обновлён
[ ] Все TODO перенесены в PROJECT_STATE.md
[ ] ADR созданы для нетривиальных решений
[ ] git add -A && git commit && git push выполнен
[ ] Промт для следующей сессии написан в конце session файла
```

---

## 10. ЭКСТРЕННОЕ ВОССТАНОВЛЕНИЕ

Если что-то сломано и не понятно что:

```bash
# 1. Смотри лог
cat docs/agent_logs.md | tail -100

# 2. Смотри последнюю сессию
ls -lt docs/sessions/ | head -5

# 3. Смотри git историю
git log --oneline -20

# 4. Откат последнего коммита (осторожно!)
git revert HEAD

# 5. Проверь сервер
curl http://localhost:8200/health
```
