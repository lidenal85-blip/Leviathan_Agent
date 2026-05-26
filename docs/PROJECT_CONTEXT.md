# Контекст проекта Leviathan — для системного промпта

## Кто я и что разрабатываю

Я разрабатываю **Leviathan** — собственную AI-агентную систему на базе Claude API. Работаю с телефона через **Termux** + **браузер**, подключаясь к удалённому серверу по SSH. Разбираюсь во многом по ходу дела, часто с помощью Claude.

---

## Инфраструктура сервера

- **ОС:** Linux (Ubuntu), сервер `/opt/leviathan_engine/agent_service/`
- **Основной стек:** Python, FastAPI, SQLite
- **Claude Code** установлен и активно используется (50+ запусков)
- **code-server** (VS Code в браузере) установлен и доступен
- **Cline** (расширение в code-server) — основной AI-инструмент для разработки
- **Cursor** — пробовал, но не разобрался (платный), переключился на Cline

---

## MCP-серверы (подключены)

### Leviathan MCP (кастомный)
- URL: `https://leviathanstory.ru/leviathan-mcp/mcp`
- Локально: `http://127.0.0.1:8300/mcp`
- Инструменты: `lev_bash`, `lev_read_file`, `lev_write_file`, `lev_patch`, `lev_find`, `lev_list_dir`, `lev_git`, `lev_health`, `lev_systemctl`, `lev_agent_task`
- Реализован на Python (`mcp_server/leviathan_mcp_server.py`)

### GitHub MCP (публичный)
- Команда: `npx -y @modelcontextprotocol/server-github`
- Токен: в env `GITHUB_PERSONAL_ACCESS_TOKEN`
- Прописан в Cline и Cursor конфигах

### Filesystem MCP (публичный, только что добавлен)
- Команда: `npx -y @modelcontextprotocol/server-filesystem`
- Доступные директории: `/opt/leviathan_engine`, `/var/www`, `/root`
- Добавлен в Cline конфиг, требует перезапуска code-server

---

## Конфиги MCP

- **Cline:** `/root/.local/share/code-server/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
- **Cursor:** `/root/.cursor/mcp.json`
- **Проект:** `/opt/leviathan_engine/agent_service/.cursor_mcp.json`

---

## Структура проекта Leviathan

```
/opt/leviathan_engine/agent_service/
├── main.py               # Основной сервер (FastAPI)
├── mcp_server/           # MCP-сервер Левиафана
├── agent/                # Агентная логика
├── api/                  # API эндпоинты
├── claude_manager/       # Управление Claude
├── config/               # Конфигурация
├── core_bridge/          # Мост к ядру
├── db/                   # База данных
├── docs/                 # Документация
├── execution/            # Исполнение задач
├── hq/                   # HQ модуль
├── logs/                 # Логи
└── .env                  # Переменные окружения
```

---

## Другие проекты на сервере (в /var/www)

- **kinovibe** — сайт (есть src-версия)
- **voice-studio** — сайт
- Возможно другие

---

## Текущий статус и задачи

- Leviathan MCP работает и доступен публично
- Cline настроен, но до конца не разобрался с некоторыми деталями
- Filesystem MCP добавлен в конфиг, но ещё не активирован (нужен перезапуск)
- Хочу использовать связку Leviathan + GitHub + Filesystem для разработки сложных сайтов через Cline

---

## Как работать со мной

- Я работаю с телефона, предпочитаю короткие чёткие ответы
- Если нужно что-то сделать на сервере — используй инструменты Leviathan MCP напрямую
- Не объясняй очевидное, действуй
- Если что-то непонятно — задай один конкретный вопрос