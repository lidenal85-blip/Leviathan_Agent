# ⚡ LEVIATHAN AGENT v3.0

> Autonomous Gemini 2.5-powered DevOps agent for the LEVIATHAN ecosystem

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green.svg)](https://fastapi.tiangolo.com)
[![Gemini](https://img.shields.io/badge/Gemini-2.5--flash-orange.svg)](https://ai.google.dev)
[![aiogram](https://img.shields.io/badge/aiogram-3.4+-blue.svg)](https://aiogram.dev)

---

## Содержание

- [Описание](#описание)
- [Архитектура](#архитектура)
- [Требования](#требования)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Запуск](#запуск)
- [API](#api)
- [Telegram-бот](#telegram-бот)
- [Веб-дашборд](#веб-дашборд)
- [Режимы выполнения](#режимы-выполнения)
- [Деплой (systemd)](#деплой-systemd)
- [MCP-интеграция (Cursor)](#mcp-интеграция-cursor)
- [Разработка](#разработка)

---

## Описание

LEVIATHAN AGENT — автономный DevOps-агент на базе **Google Gemini 2.5**, способный самостоятельно выполнять задачи: проверять состояние серверов, управлять файлами, запускать команды, работать с git и взаимодействовать с внешними сервисами.

Агент принимает задачи через REST API, Telegram-бот или веб-дашборд, выполняет их пошагово (итерационный loop) и отчитывается о результате.

**Ключевые особенности:**
- **Пул ключей Gemini** — автоматическая ротация до 14 API-ключей при исчерпании лимита
- **Идемпотентность** — OperationRegistry защищает от дублирующих действий при повторном запуске
- **ExecutionJournal** — полный журнал всех шагов с метриками LLM (токены, задержка, кол-во вызовов)
- **3 режима безопасности** — от read-only до полного доступа
- **WebSocket live-лог** — события в реальном времени в браузере
- **systemd-ready** — полноценный деплой как системный сервис

---

## Архитектура

```
leviathan_agent/
├── main.py                   # FastAPI точка входа, WebSocket, дашборд
├── agent/
│   ├── core.py               # LeviathanAgent — основной цикл планирования и выполнения
│   └── tg_bot.py             # AgentRunner, TelegramNotifier, хэндлеры Telegram
├── config/
│   └── settings.py           # Pydantic-Settings, читает .env
├── core_bridge/
│   └── key_pool.py           # KeyPool — ротация Gemini API-ключей
├── db/
│   ├── journal.py            # ExecutionJournal — журнал шагов (SQLite)
│   └── storage.py            # TaskStorage — хранение задач (SQLite)
├── execution/
│   └── idempotency.py        # OperationRegistry — защита от дублей
├── mcp_server/
│   └── leviathan_mcp.py      # MCP-сервер для интеграции с Cursor IDE
├── docs/                     # Документация
├── .env.example              # Пример конфигурации
├── requirements.txt          # Зависимости Python
├── deploy.sh                 # Скрипт деплоя на Linux-сервер
└── leviathan_agent.service   # systemd unit-файл
```

**Поток данных:**
```
Telegram / REST API / WebUI
        │
        ▼
   AgentRunner (очередь задач)
        │
        ▼
   LeviathanAgent (итерационный loop)
        │  ├── KeyPool → Gemini 2.5 Flash API
        │  ├── ExecutionJournal (логирование)
        │  └── OperationRegistry (идемпотентность)
        │
        ▼
   TaskStorage (результат) + WebSocket (live-события)
```

---

## Требования

- Python 3.11+
- Linux (Ubuntu 22.04+ рекомендуется для systemd-деплоя)
- Минимум 1 Google Gemini API-ключ (модель `gemini-2.5-flash`)
- Опционально: Telegram Bot Token для управления через бот

---

## Установка

```bash
git clone https://github.com/lidenal85-blip/Leviathan_Agent.git
cd Leviathan_Agent

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

---

## Конфигурация

Скопируй `.env.example` в `.env` и заполни значения:

```bash
cp .env.example .env
nano .env
```

### Обязательные параметры

| Параметр | Описание |
|---|---|
| `GEMINI_K1` | Google Gemini API-ключ (обязательно минимум один) |
| `GEMINI_MODEL` | Модель Gemini (по умолчанию `gemini-2.5-flash`) |

### Telegram (опционально)

| Параметр | Описание |
|---|---|
| `TG_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `TG_ADMIN_CHAT_ID` | Chat ID администратора (числовой) |

Если Telegram не настроен, агент работает в режиме REST API + веб-дашборд.

### Полный список параметров

```dotenv
# Gemini API-ключи (до 14 штук)
GEMINI_K1=AIza...
GEMINI_K2=AIza...
# ...до GEMINI_K14

# Модель
GEMINI_MODEL=gemini-2.5-flash

# Telegram
TG_BOT_TOKEN=
TG_ADMIN_CHAT_ID=

# GitHub (для инструмента git_commit_push)
GITHUB_TOKEN=

# Параметры агента
MAX_ITERATIONS=50         # Макс. шагов на задачу
DEFAULT_MODE=NORMAL       # Режим по умолчанию
TOOL_TIMEOUT_SEC=60       # Таймаут одного инструмента
MAX_FILE_SIZE_KB=100      # Макс. размер файла для чтения

# Сервер
HOST=0.0.0.0
PORT=8200
```

---

## Запуск

### Локально (разработка)

```bash
source venv/bin/activate
python main.py
```

Или через uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port 8200 --reload
```

### Проверка

```bash
curl http://localhost:8200/health
```

---

## API

Базовый URL: `http://localhost:8200`

### `GET /health`

Статус агента.

```json
{
  "status": "ok",
  "version": "3.0.0",
  "current_task": null,
  "key_pool": [{"key_idx": 0, "available": true, "calls": 12}],
  "queue_size": 0
}
```

### `POST /api/tasks`

Создать задачу.

```json
// Request
{
  "prompt": "Проверь, что nginx слушает 443 порт, и покажи 20 последних строк лога",
  "mode": "NORMAL"
}

// Response 201
{
  "task_id": "abc123",
  "status": "pending"
}
```

### `GET /api/tasks`

Список последних задач (параметр `?limit=20`).

### `GET /api/tasks/{task_id}`

Детали задачи: шаги, статус, результат, статистика LLM.

### `DELETE /api/tasks/current`

Отменить текущую выполняемую задачу.

### `GET /api/pool`

Статус пула Gemini-ключей.

### `WS /ws`

WebSocket — live-события выполнения задач.

---

## Telegram-бот

Если заданы `TG_BOT_TOKEN` и `TG_ADMIN_CHAT_ID`, агент запускает Telegram-бота.

**Команды:**
| Команда | Описание |
|---|---|
| `/task <текст>` | Создать задачу в режиме NORMAL |
| `/safe <текст>` | Создать задачу в режиме SAFE (только чтение) |
| `/full <текст>` | Создать задачу в режиме FULL |
| `/status` | Статус текущей задачи и очереди |
| `/cancel` | Отменить текущую задачу |

---

## Веб-дашборд

Откройте `http://localhost:8200` — встроенный дашборд с:
- Формой для создания задач
- Выбором режима выполнения
- Live-логом через WebSocket
- Историей задач (кликни для деталей: шаги, токены, длительность)

---

## Режимы выполнения

| Режим | Описание | Ограничения |
|---|---|---|
| `SAFE` | Только чтение | Никаких записей, изменений, команд |
| `NORMAL` | Стандартный (рекомендуется) | Нет git push, нет опасных команд |
| `FULL` | Полный доступ | Все инструменты включая git push |

---

## Деплой (systemd)

```bash
# На целевом сервере
sudo bash deploy.sh
```

Скрипт:
1. Клонирует или обновляет репозиторий в `/opt/leviathan_engine/agent_service`
2. Создаёт Python venv и устанавливает зависимости
3. Создаёт `.env` из `.env.example` (если не существует)
4. Регистрирует и запускает systemd-сервис `leviathan_agent`

**Управление сервисом:**
```bash
sudo systemctl status leviathan_agent
sudo systemctl restart leviathan_agent
sudo journalctl -u leviathan_agent -f
```

---

## MCP-интеграция (Cursor)

LEVIATHAN AGENT поддерживает протокол MCP для интеграции с Cursor IDE.

**Настройка в Cursor:**

`.cursor_mcp.json` уже содержит конфигурацию:
```json
{
  "mcpServers": {
    "leviathan": {
      "command": "python3",
      "args": ["/opt/leviathan_agent/mcp_server/leviathan_mcp.py"],
      "env": {
        "LEVIATHAN_URL": "http://localhost:8200",
        "ARBITR_URL": "http://localhost:8090"
      }
    }
  }
}
```

> ⚠️ Убедись, что путь в `args` совпадает с реальным расположением `mcp_server/leviathan_mcp.py` на твоей машине.

---

## Разработка

### Структура задачи (Task)

Каждая задача проходит цикл: `pending → running → done | failed | cancelled`

Шаги задачи фиксируются в `ExecutionJournal` с полями:
- `tool` — имя вызванного инструмента
- `invocation_id` — уникальный ID вызова
- `idempotency_key` — ключ для защиты от дублей
- `duration` — время выполнения шага
- `result` — результат (`ok: bool`, данные)

### Добавление нового инструмента

Инструменты определяются в `agent/core.py`. Каждый инструмент — это async-функция с аннотацией типов. Агент выбирает инструменты автоматически на основе промпта задачи.

---

## Лицензия

Proprietary — LEVIATHAN Ecosystem
