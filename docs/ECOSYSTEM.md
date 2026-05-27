# LEVIATHAN ECOSYSTEM — Карта экосистемы

> Создано: 2026-05-28  
> Статус: Фундамент заложен, фаза 1-2 в разработке

---

## Концепция

**LEVIATHAN** — экосистема автономных производственных сервисов, управляемая одним интеллектуальным ядром — **LEVIATHAN Agent**.
Агент не просто выполняет задачи — он **наблюдает за своими продуктами**, принимает решения и вызывает нужные модули сам.

```
┌──────────────────────────────────────────────────────┐
│          LEVIATHAN AGENT  (port 8200)           │
│  Мозг: оркестрация, мониторинг, решения  │
│  LLM: Gemini 2.5-flash + Groq + Claude          │
│  Telegram: @Levi_Engi_bot                       │
└───────┬──────────────┬──────────────┬───────┘
         │              │              │
         ▼              ▼              ▼
┌────────────┐ ┌────────────┐ ┌────────────┐
│  PROD-1  │ │   PROD-2   │ │   PROD-3   │
│  :8210   │ │   :8220    │ │   :8230    │
│ Book    │ │  Book      │ │ Textbook  │
│ Factory │ │  Downloader│ │ Platform  │
└────────────┘ └────────────┘ └────────────┘
     │                                  [PROD-N :82XX]
     ▼                                  ← будущие модули
┌─────────────────┐
│ MetricsBridge :8240 │  ← мониторинг всех продуктов
└─────────────────┘
```

---

## Серверная карта

| Порт | Сервис | Путь | Статус |
|------|---------|------|---------|
| 8200 | LEVIATHAN Agent | `/opt/leviathan_engine/agent_service/` | ✅ Работает |
| 8095 | ArbitrCockpit | `/opt/arbitr_cockpit/` | ✅ Работает |
| 8210 | Book Factory (LEVIATHAN_refactored) | `/opt/book_factory/` | 🔴 Не задеплоен |
| 8220 | Book Downloader | `/opt/book_downloader/` | 🔴 Не задеплоен |
| 8230 | Textbook Platform | `/opt/textbook_platform/` | 🔴 Не задеплоен |
| 8240 | MetricsBridge | `/opt/leviathan_engine/agent_service/` | 🔴 Не реализован |
| 8300 | MCP Server | `/opt/leviathan_engine/agent_service/` | ✅ Работает |
| 8110 | KinoVibe | `/var/www/kinovibe/` | ✅ Работает |
| 8120 | VoiceStudio | `/var/www/voicestudio/` | ✅ Работает |

**Свободные порты для будущих модулей:** 8250, 8260, 8270, 8280

---

## Продукты экосистемы

### PROD-1: Book Factory — Книжная фабрика (port 8210)

**Источник:** `LEVIATHAN_refactored` (v5)

| Параметр | Значение |
|----------|--------|
| Назначение | Генерация художественных текстов |
| Агентов | 27 (chapter_writer, lore_keeper, critic, world_builder, ...) |
| LLM | Gemini 2.5-flash + thinking tokens, Groq llama-3.3-70b |
| Стораж | ChromaDB (RAG), SQLite, GhostBin |
| ТТС | edge-tts |
| Статус | Блоки 1-4 ✅ (81 файл, все тесты зелёные) |
| WIP | Блок 5: researcher, style_memory, coauthor, poet, navigator, mentor |
| Особенность | Revisor читает thinking-токены Gemini до ответа — уникальная функция |

**Ключевые endpointы (план):**
```
POST /api/book/create     ← создать книгу (жанр, промпт)
GET  /api/book/{id}/status ← статус генерации
GET  /api/book/{id}/export ← скачать готовую книгу
GET  /metrics              ← кол-во книг, токены, время
GET  /health
```

---

### PROD-2: Book Downloader — Скачивание книг (port 8220)

**Источник:** `book_downloader`

| Параметр | Значение |
|----------|--------|
| Назначение | Скачивание, конвертация, перевод книг |
| Модули | converter, sources (WIP), torrent, translator |
| PDF | WeasyPrint (CSS Paged Media) |
| Статус | Структура есть, `sources/` не дописан |

**Ключевые endpointы (план):**
```
POST /api/download        ← скачать книгу по URL/названию
POST /api/convert         ← конвертировать формат (epub→pdf, fb2→epub)
POST /api/translate       ← перевести книгу
GET  /api/book/{id}       ← статус задачи
GET  /metrics
GET  /health
```

---

### PROD-3: Textbook Platform — Образовательная платформа (port 8230)

**Источник:** `textbook_platform`

| Параметр | Значение |
|----------|--------|
| Назначение | Размещение и чтение учебников, AI-наставник |
| Модули | ai_cabinet, progress, bookmarks, checklist, mentor |
| Особенность | Whisper для транскрипции видео, yt-dlp |
| Статус | MVP есть, сервисы не завершены |

**Ключевые endpointы (план):**
```
POST /api/book/upload     ← загрузить учебник
GET  /api/book/{id}       ← читать главу
GET  /api/student/progress ← прогресс студента
POST /api/mentor/ask      ← вопрос AI-наставнику
GET  /metrics
GET  /health
```

---

### PROD-4 (future): MetricsBridge (port 8240)

Агрегатор метрик всех продуктов. Агент опрашивает его, принимает решения.

```
GET  /metrics/all         ← метрики всех сервисов
GET  /metrics/{service}   ← метрики одного сервиса
POST /decision            ← агент принял решение
```

---

## Петля замыкания (целевая модель)

```
Agent-Scout нашёл заявку на Kwork: "напиши книгу по X"
    ↓
LEVIATHAN Agent вызывает Book Factory API
    ↓
Book Factory генерирует текст (27 агентов)
    ↓
Book Downloader конвертирует в PDF/EPUB
    ↓
Agent публикует (телеграм, сайт, Amazon KDP)
    ↓
MetricsBridge собирает метрики: просмотры, конверсия
    ↓
Agent видит: конверсия < 2%
    ↓
Agent решает: запустить SEO-оптимизацию → вызывает seo_tool()
    ↓
если не помогает → решает: реклама → вызывает ads_tool()
```

---

## Принципы экосистемы

1. **Каждый продукт — отдельный сервис.** Свой порт, свой systemd, своя БД, свой git-репо (optional).
2. **Связь через HTTP REST.** Агент вызывает сервисы через httpx. Не через прямой import.
3. **Каждый сервис експортирует** `GET /health` и `GET /metrics`.
4. **Метрики хранит каждый сервис** сам. Агент агрегирует через MetricsBridge.
5. **Агент пишет новые модули сам.** Long-term цель: агент анализирует потребность и генерирует новый `tools_*.py` + сервис по шаблону.