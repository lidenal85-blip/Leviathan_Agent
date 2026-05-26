# TZ: Leviathan Agent Level 6 — Autonomous Project Executor

> **СТАТУС:** Следующий этап после завершения ClaudeAdapter + LLMProviderPool
> **Зависимости:** ClaudeAdapter → LLMProviderPool → затем Level 6

---

## Цель

Агент получает задачу (например, "сделай интернет-магазин"), сам разбивает на шаги, выполняет их последовательно, переключает аккаунты при лимитах. Человек даёт задачу и получает результат.

---

## Архитектурная привязка (правка относительно оригинального ТЗ)

Исходное ТЗ кладёт модули в core_bridge/ и db/ — но это противоречит текущей архитектуре.

**Правильное расположение:**

```
claude_manager/domain/projects/          ← новая папка
    task_planner.py     (TaskPlanner)
    project_executor.py (ProjectExecutor)
claude_manager/core/storage/
    project_store.py    (ProjectStore, таблицы SQLite)
```

TaskPlanner вызывает LLMProviderPool (не ClaudeAdapter напрямую!) — чтобы получать автоматическую ротацию аккаунтов.

---

## Модуль 1: TaskPlanner

Файл: `claude_manager/domain/projects/task_planner.py`

```python
class TaskPlanner:
    async def decompose(self, goal: str, context: str = "") -> List[SubTask]:
        """
        goal: "Сделай интернет-магазин на FastAPI + React"
        Возвращает: [
            {"id": 1, "description": "...", "done": False, "result": ""},
            ...
        ]
        """
```

Реализация: один промпт к LLMProviderPool, Claude разбивает на 5-15 шагов, парсим JSON.

---

## Модуль 2: ProjectStore

Файл: `claude_manager/core/storage/project_store.py`

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    goal TEXT,
    status TEXT,  -- planning, executing, waiting, done, failed
    current_step INTEGER DEFAULT 0,
    created_at TIMESTAMP
);

CREATE TABLE project_steps (
    project_id TEXT,
    step_index INTEGER,
    description TEXT,
    status TEXT,  -- pending, running, done, failed
    result TEXT,
    error TEXT,
    account_used TEXT,
    PRIMARY KEY (project_id, step_index)
);
```

---

## Модуль 3: ProjectExecutor

Файл: `claude_manager/domain/projects/project_executor.py`

```python
class ProjectExecutor:
    async def start_project(self, goal: str, session_id: str) -> str: ...
    async def execute_step(self, project_id: str) -> bool: ...
    async def resume_project(self, project_id: str): ...
```

Логика шага:
1. Взять описание шага
2. Промпт: "Ты выполняешь проект. Текущий шаг: {description}. Предыдущие: {results}. Сделай."
3. Отправить через LLMProviderPool (Claude → при лимите Gemini)
4. Сохранить результат в ProjectStore
5. Перейти к следующему шагу

---

## Telegram команды

| Команда | Описание |
|---|---|
| /project Текст задачи | Начать новый проект |
| /status {id} | Прогресс проекта |
| /pause {id} | Пауза |
| /resume {id} | Продолжить |
| /projects | Список всех |

---

## Изменения в существующих файлах

| Файл | Что добавить |
|---|---|
| agent/core.py | `self.project_executor = ProjectExecutor(llm_pool)` |
| agent/tg_bot.py | Обработчики /project, /status, /pause, /resume |
| main.py | REST эндпойнты для проектов (опционально) |

---

## Критерий готовности

```
/project Сделай todo-лист на FastAPI + HTML. SQLite. Команды в шаблоны.
```

Агент сам:
1. Разбивает на шаги
2. Выполняет каждый шаг
3. При лимите Claude — переключается автоматически
4. Говорит: "Готово. Запусти: python app.py"

---

## Оценка ТЗ и архитектурные правки

**Цель:** правильная
**Расположение модулей:** исправлено (см. выше)
**TaskPlanner:** должен ходить через LLMProviderPool, не напрямую к ClaudeAdapter
**Порядок реализации:** ClaudeAdapter → LLMProviderPool → Level 6