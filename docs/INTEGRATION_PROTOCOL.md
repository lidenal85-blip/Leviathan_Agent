# LEVIATHAN ECOSYSTEM — Протокол Интеграции

> Как агент вызывает сервисы экосистемы

---

## 1. Общие правила

- Связь через HTTP REST (localhost), агент → сервис
- Транспорт: `httpx.AsyncClient` (уже есть в стеке)
- Цепочка: `agent/tools_factory.py` → HTTP → сервис
- Таймаут: 30 секунд на запрос
- Ошибки поглощает агент, не пробрасывает наружу

---

## 2. Стандартный health-ответ (обязателен для каждого сервиса)

```json
GET /health
{
  "status": "ok",
  "service": "book_factory",
  "version": "1.0.0",
  "port": 8210,
  "uptime_sec": 3600
}
```

## 3. Стандартный metrics-ответ

```json
GET /metrics
{
  "service": "book_factory",
  "tasks_total": 42,
  "tasks_running": 2,
  "tasks_completed": 38,
  "tasks_failed": 2,
  "tokens_used": 1500000,
  "last_activity": "2026-05-28T12:00:00Z",
  "custom": {}
}
```

## 4. Стандартный POST-ответ

```json
// Request:
{ "task_id": "uuid", "payload": {} }

// Response:
{ "ok": true, "job_id": "uuid", "status": "accepted", "result": null, "error": null }
```

## 5. Порты (добавить в config/settings.py)

```python
BOOK_FACTORY_URL    = "http://localhost:8210"
BOOK_DOWNLOADER_URL = "http://localhost:8220"
TEXTBOOK_URL        = "http://localhost:8230"
METRICS_BRIDGE_URL  = "http://localhost:8240"
```

## 6. Структура tools_factory.py (контракт)

```python
# agent/tools_factory.py
async def call_book_factory(task: str, payload: dict) -> dict: ...
async def call_book_downloader(task: str, payload: dict) -> dict: ...
async def call_textbook(task: str, payload: dict) -> dict: ...
async def get_ecosystem_metrics() -> dict: ...
```

## 7. DecisionEngine — петля замыкания

| Триггер | Действие | Инструмент |
|---------|----------|-------------|
| `book.views_7d < 50` | SEO-оптимизация | `call_seo_tool()` |
| `book.conversion < 0.02` | Реклама | `call_ads_tool()` |
| `book.errors_count > 5` | Перегенерация | `call_book_factory("fix_chapter")` |
| `downloader.queue > 20` | Алерт | `notify_admin()` |