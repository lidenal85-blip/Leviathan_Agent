# LEVIATHAN ECOSYSTEM — Роадмап

> Создан: 2026-05-28  
> Это долгосрочный план. Каждая фаза — отдельная сессия.

---

## ФАЗА 0 — Документация экосистемы ✅

> Статус: Завершено 2026-05-28

- [x] `docs/ECOSYSTEM.md` — карта всех продуктов, порты, статусы
- [x] `docs/INTEGRATION_PROTOCOL.md` — контракт взаимодействия
- [x] `docs/ROADMAP.md` — этот файл

---

## ФАЗА 1 — Достроить агента (Core Engine)

> Приоритет: Высокий | Оценка: 2-3 сессии

### 1.1 PAUSED state + hot-resume
- [ ] Добавить `TaskStatus.PAUSED` в `agent/core.py` (сейчас есть: PENDING/RUNNING/WAITING/DONE/FAILED)
- [ ] Сохранять `current_step` + `steps_data` в SQLite при паузе
- [ ] При старте агента: если есть PAUSED/RUNNING → восстановить из БД и продолжить
- [ ] UPSERT мутации (не перезаписывать, добавлять к истории)

### 1.2 429-backoff + _check_api_gate()
- [ ] `try-except` вокруг всех HTTP-вызовов (Gemini, Groq, Claude)
- [ ] При 429/quota: таск → PAUSED, запись в `logs/pipeline.log`
- [ ] Фоновый `asyncio.sleep` на 1-3 часа
- [ ] `_check_api_gate()`: ping `"ping"` на gemini-2.5-flash или llama-3-8b
- [ ] 200 → PAUSED→RUNNING, горячий рестарт с последнего шага
- [ ] 429 снова → +1 час, лог

### 1.3 logs/pipeline.log
- [ ] Создать `logs/pipeline.log` (сейчас есть `logs/claude_manager.log`)
- [ ] Формат: `[2026-05-28 12:00:00] TASK_ID | EVENT | деталь`
- [ ] События: 429_caught | backoff_start | gate_ping | gate_ok | resume | complete

### 1.4 Fire-and-forget режим
- [ ] Молчание во время выполнения (нет промежуточных сообщений в TG)
- [ ] Одна финальная строка: `Конвейер завершён. Результат: [path/текст]`

### 1.5 Level 6 (остаток)
- [ ] `claude_manager/domain/projects/resume_manager.py`
- [ ] `claude_manager/domain/projects/project_orchestrator.py`
- [ ] Фикс `test_pool.py` SyntaxError (стр.242)

---

## ФАЗА 2 — Деплой продуктов как сервисов

> Приоритет: Средний | Оценка: 3-4 сессии

### 2.1 Book Factory (LEVIATHAN_refactored → :8210)
- [ ] Прописать Блок 5 (остаток агентов)
- [ ] Добавить `GET /health`, `GET /metrics` в FastAPI
- [ ] Создать `book_factory.service` (systemd)
- [ ] Пусть на порт 8210
- [ ] Запустить на сервере (`/opt/book_factory/`)

### 2.2 Book Downloader (:8220)
- [ ] Дописать `modules/services/sources/` (ISBN, LibGen, OpenLibrary)
- [ ] Добавить `/health`, `/metrics`
- [ ] Создать `book_downloader.service`
- [ ] Пусть на порт 8220

### 2.3 Textbook Platform (:8230)
- [ ] Дописать `modules/services/` (progress, mentor)
- [ ] Добавить `/health`, `/metrics`
- [ ] Создать `textbook_platform.service`
- [ ] Пусть на порт 8230

---

## ФАЗА 3 — Инструменты агента для вызова продуктов

> Приоритет: Средний | Оценка: 1-2 сессии

- [ ] `agent/tools_factory.py` — `call_book_factory()`, `call_book_downloader()`, `call_textbook()`
- [ ] Добавить URLы в `config/settings.py` (BOOK_FACTORY_URL и др.)
- [ ] Зарегистрировать инструменты в `agent/core.py`
- [ ] `get_ecosystem_metrics()` — агрегация /metrics со всех сервисов

---

## ФАЗА 4 — Петля замыкания (DecisionEngine + MetricsBridge)

> Приоритет: Низкий (после фаз 1-3) | Оценка: 2-3 сессии

- [ ] `db/metrics.py` — одна таблица метрик для всех продуктов
- [ ] `claude_manager/domain/monitor/decision_engine.py` — таблица правил
- [ ] Фоновый цикл проверки: каждые N минут → опрашивает /metrics → принимает решения
- [ ] MetricsBridge API (:8240)

---

## ФАЗА 5 — Агент пишет модули сам (долгосрочно)

> Предусловие: фазы 1-4 завершены

- [ ] CodeGen-инструмент: агент генерирует `tools_*.py` по шаблону
- [ ] Scaffolding: создаёт структуру нового сервиса по образцу
- [ ] Self-testing: запускает `pytest` на сгенерированный код
- [ ] Агент оценивает нужность нового модуля по метрикам

---

## Следующие модули (Tier 3-4 из исходного документа)

Обсуждается отдельно после завершения фаз 1-3.

| Модуль | Порт | Приоритет |
|---------|------|----------|
| SEO Engineer | 8250 | Средний |
| SMM Publisher | 8260 | Средний |
| Video Showrunner (FFmpeg/TTS) | 8270 | Низкий |
| Media Buyer / Ads | — | Отдельное обсуждение |