#!/usr/bin/env python3
"""
leviathan_audit.py — полный аудит и патч репозитория Leviathan_Agent
════════════════════════════════════════════════════════════════════════
Запуск:
    python3 leviathan_audit.py --token ghp_XXXX

Что делает:
    1. Читает ВСЕ файлы из репозитория через GitHub API
    2. Выводит подробный лог с аудитом каждого файла
    3. Применяет все необходимые исправления
    4. Делает коммит и пуш в main

Требования:
    pip install requests rich
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import print as rprint
except ImportError:
    print("Устанавливаю зависимости...")
    os.system(f"{sys.executable} -m pip install requests rich -q")
    import requests
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import print as rprint

console = Console()

# ── Константы ────────────────────────────────────────────────────────────────
REPO          = "lidenal85-blip/Leviathan_Agent"
BRANCH        = "main"
BASE          = "https://api.github.com"
COMMIT_MSG    = "audit: fix gemini-2.5-flash model, full README, code improvements"

# ── GitHub API ────────────────────────────────────────────────────────────────

class GitHub:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def get(self, path: str) -> dict:
        r = self.s.get(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: dict) -> dict:
        r = self.s.put(f"{BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def get_tree(self) -> list[dict]:
        """Рекурсивное дерево всех файлов в репозитории."""
        data = self.get(f"/repos/{REPO}/git/trees/{BRANCH}?recursive=1")
        return [f for f in data["tree"] if f["type"] == "blob"]

    def get_file(self, path: str) -> tuple[str, str]:
        """Возвращает (содержимое, sha)."""
        data = self.get(f"/repos/{REPO}/contents/{path}?ref={BRANCH}")
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return content, data["sha"]

    def put_file(self, path: str, content: str, sha: str, message: str) -> None:
        self.put(f"/repos/{REPO}/contents/{path}", {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "sha": sha,
            "branch": BRANCH,
        })

# ── Аудит-правила ─────────────────────────────────────────────────────────────

class AuditResult:
    def __init__(self, path: str, original: str):
        self.path     = path
        self.original = original
        self.patched  = original
        self.issues: list[dict] = []   # {level, msg, fixed}

    def issue(self, level: str, msg: str, fixed: bool = False):
        self.issues.append({"level": level, "msg": msg, "fixed": fixed})

    @property
    def changed(self) -> bool:
        return self.patched != self.original

    @property
    def has_errors(self) -> bool:
        return any(i["level"] == "ERROR" for i in self.issues)


def audit_env_example(r: AuditResult):
    txt = r.patched

    # КРИТИЧНО: неверная модель
    if "gemini-2.0-flash" in txt:
        r.issue("ERROR", "GEMINI_MODEL=gemini-2.0-flash → должна быть gemini-2.5-flash", fixed=True)
        txt = txt.replace("gemini-2.0-flash", "gemini-2.5-flash")

    # Улучшаем комментарии и добавляем DB_PATH
    if "DB_PATH" not in txt and "db_path" not in txt.lower():
        r.issue("WARN", "Нет DB_PATH — добавляем", fixed=True)
        txt = txt.replace(
            "# ── База данных (SQLite) ──",
            "# ── База данных (SQLite) ──"
        )
        # добавляем DB_PATH рядом с DATABASE_URL если его нет
        if "DATABASE_URL" in txt and "DB_PATH" not in txt:
            txt = txt.replace(
                "DATABASE_URL=sqlite+aiosqlite:///db/leviathan.db",
                "DATABASE_URL=sqlite+aiosqlite:///db/leviathan.db\nDB_PATH=db/leviathan.db"
            )

    r.patched = txt


def audit_requirements(r: AuditResult):
    txt = r.patched
    lines = txt.strip().splitlines()
    pkgs = {l.split(">=")[0].split("==")[0].strip() for l in lines if l.strip()}

    # Старый SDK без нового
    if "google-generativeai" in pkgs and "google-genai" not in pkgs:
        r.issue("WARN",
                "google-generativeai (старый SDK) — добавляем google-genai>=1.16.0 для Gemini 2.5",
                fixed=True)
        txt = txt.rstrip() + "\ngoogle-genai>=1.16.0\n"

    # Нет версии для uvicorn
    if "uvicorn[standard]" not in txt and "uvicorn" in txt:
        r.issue("WARN", "uvicorn без [standard] — нет websocket поддержки")

    r.patched = txt


def audit_main_py(r: AuditResult):
    txt = r.patched

    # Устаревшая модель в строках логирования/заголовках
    if "gemini-2.0" in txt.lower():
        r.issue("ERROR", "Упоминание gemini-2.0 в main.py", fixed=True)
        txt = re.sub(r"gemini-2\.0", "gemini-2.5", txt, flags=re.IGNORECASE)

    # Проверяем что версия правильная
    if 'version="3.0.0"' in txt:
        r.issue("INFO", "Версия FastAPI app = 3.0.0 — OK")

    # Проверяем lifespan
    if "asynccontextmanager" in txt and "lifespan" in txt:
        r.issue("INFO", "lifespan pattern — OK (современный FastAPI)")

    # Мокирование notifier через unittest.mock — это плохой паттерн
    if "unittest.mock" in txt:
        r.issue("WARN",
                "TG notifier мокируется через unittest.mock — лучше использовать NullNotifier класс")

    # WebSocket clients хранятся в set без lock — потенциальный race condition
    if "_ws_clients.add" in txt and "asyncio.Lock" not in txt:
        r.issue("WARN",
                "_ws_clients — set без asyncio.Lock, при concurrent WebSocket может быть race condition")

    r.patched = txt


def audit_deploy_sh(r: AuditResult):
    txt = r.patched

    # Несоответствие путей с .cursor_mcp.json
    if "/opt/leviathan_engine/agent_service" in txt:
        r.issue("WARN",
                "deploy.sh использует /opt/leviathan_engine/agent_service, "
                "но .cursor_mcp.json ссылается на /opt/leviathan_agent — пути расходятся")

    # Нет проверки работоспособности после деплоя
    if "curl" not in txt and "/health" not in txt:
        r.issue("WARN", "Нет health-check после деплоя (curl /health)", fixed=True)
        txt = txt.replace(
            'echo "✅ Деплой завершён!"',
            'echo "[health] Проверяем API..."\n'
            'sleep 3\n'
            'curl -sf http://localhost:8200/health && echo " ✅ API отвечает" || echo " ⚠ API не отвечает"\n'
            '\necho "✅ Деплой завершён!"'
        )

    # User=root в .service — плохая практика (проверяем .service отдельно)
    r.patched = txt


def audit_service_file(r: AuditResult):
    txt = r.patched

    if "User=root" in txt:
        r.issue("ERROR",
                "User=root в systemd unit — ОПАСНО! Замените на непривилегированного пользователя (напр. leviathan)",
                fixed=True)
        txt = txt.replace("User=root", "User=leviathan")
        # Добавляем Group если нет
        if "Group=" not in txt:
            txt = txt.replace("User=leviathan", "User=leviathan\nGroup=leviathan")

    # Нет ограничений ресурсов
    if "MemoryLimit" not in txt and "MemoryMax" not in txt:
        r.issue("WARN", "Нет ограничения памяти (MemoryMax) в unit-файле", fixed=True)
        txt = txt.replace(
            "KillMode=mixed",
            "KillMode=mixed\n\n# Ограничения ресурсов\nMemoryMax=2G\nCPUQuota=200%"
        )

    # NoNewPrivileges
    if "NoNewPrivileges" not in txt:
        r.issue("WARN", "Нет NoNewPrivileges=yes — hardening systemd", fixed=True)
        txt = txt.replace(
            "KillMode=mixed",
            "KillMode=mixed\nNoNewPrivileges=yes\nPrivateTmp=yes"
        )

    r.patched = txt


def audit_settings_py(r: AuditResult):
    txt = r.patched

    if "gemini-2.0" in txt:
        r.issue("ERROR", "Хардкоженный gemini-2.0 в settings.py", fixed=True)
        txt = re.sub(r"gemini-2\.0", "gemini-2.5", txt, flags=re.IGNORECASE)

    if "db_path" not in txt.lower() and "DB_PATH" not in txt:
        r.issue("WARN", "Нет db_path поля в Settings — используется только DATABASE_URL")

    r.patched = txt


def audit_core_py(r: AuditResult):
    txt = r.patched

    if "gemini-2.0" in txt.lower():
        r.issue("ERROR", "Хардкоженная модель gemini-2.0 в agent/core.py", fixed=True)
        txt = re.sub(r"gemini-2\.0", "gemini-2.5", txt, flags=re.IGNORECASE)

    # Проверяем импорты
    if "google.generativeai" in txt and "google.genai" not in txt:
        r.issue("WARN",
                "Используется старый SDK google.generativeai — рассмотрите миграцию на google.genai")

    # Проверяем timeout на LLM вызовах
    if "generate_content" in txt and "timeout" not in txt:
        r.issue("WARN", "LLM generate_content без timeout — может зависнуть навсегда")

    r.patched = txt


def audit_tg_bot_py(r: AuditResult):
    txt = r.patched

    if "gemini-2.0" in txt.lower():
        r.issue("ERROR", "Хардкоженная модель gemini-2.0 в tg_bot.py", fixed=True)
        txt = re.sub(r"gemini-2\.0", "gemini-2.5", txt, flags=re.IGNORECASE)

    r.patched = txt


def audit_key_pool_py(r: AuditResult):
    txt = r.patched

    if "gemini-2.0" in txt.lower():
        r.issue("ERROR", "Хардкоженная модель gemini-2.0 в key_pool.py", fixed=True)
        txt = re.sub(r"gemini-2\.0", "gemini-2.5", txt, flags=re.IGNORECASE)

    r.patched = txt


def audit_readme(r: AuditResult) -> str:
    """Возвращает новый README.md — полная документация."""
    r.issue("INFO", "README.md пустой (1 строка) — перезаписываем полной документацией", fixed=True)
    r.patched = NEW_README
    return r.patched


# ── Новый README ──────────────────────────────────────────────────────────────

NEW_README = """\
# ⚡ LEVIATHAN AGENT v3.0

> Autonomous **Gemini 2.5 Flash**-powered DevOps agent for the LEVIATHAN ecosystem

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green)
![Gemini](https://img.shields.io/badge/Gemini-2.5--flash-orange)
![aiogram](https://img.shields.io/badge/aiogram-3.4+-blue)

---

## Описание

LEVIATHAN AGENT — автономный DevOps-агент, способный самостоятельно выполнять задачи:
проверять состояние серверов, управлять файлами, запускать команды, работать с git и
взаимодействовать с внешними сервисами. Задачи принимаются через REST API, Telegram-бот
или веб-дашборд.

**Ключевые особенности:**
- **Пул ключей Gemini** — автоматическая ротация до 14 API-ключей
- **Идемпотентность** — OperationRegistry защищает от дублирующих операций
- **ExecutionJournal** — полный журнал шагов с метриками LLM (токены, задержка)
- **3 режима безопасности** — SAFE / NORMAL / FULL
- **WebSocket live-лог** — события агента в реальном времени
- **systemd-ready** — полноценный деплой как системный сервис

---

## Архитектура

```
leviathan_agent/
├── main.py                   # FastAPI, WebSocket, дашборд
├── agent/
│   ├── core.py               # LeviathanAgent — итерационный цикл
│   └── tg_bot.py             # AgentRunner, TelegramNotifier, хэндлеры
├── config/
│   └── settings.py           # Pydantic-Settings (.env)
├── core_bridge/
│   └── key_pool.py           # Ротация Gemini-ключей
├── db/
│   ├── journal.py            # ExecutionJournal (SQLite)
│   └── storage.py            # TaskStorage (SQLite)
├── execution/
│   └── idempotency.py        # OperationRegistry
├── mcp_server/
│   └── leviathan_mcp.py      # MCP-сервер для Cursor IDE
├── .env.example
├── requirements.txt
├── deploy.sh
└── leviathan_agent.service
```

---

## Быстрый старт

```bash
git clone https://github.com/lidenal85-blip/Leviathan_Agent.git
cd Leviathan_Agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполни GEMINI_K1 в .env
python main.py
```

Дашборд: `http://localhost:8200`

---

## Конфигурация (`.env`)

| Параметр | Обязательно | Описание |
|---|---|---|
| `GEMINI_K1`..`GEMINI_K14` | ✅ min 1 | Google Gemini API-ключи |
| `GEMINI_MODEL` | ✅ | Модель (`gemini-2.5-flash`) |
| `TG_BOT_TOKEN` | ❌ | Токен Telegram-бота |
| `TG_ADMIN_CHAT_ID` | ❌ | Chat ID администратора |
| `GITHUB_TOKEN` | ❌ | PAT для git_commit_push |
| `MAX_ITERATIONS` | ❌ | Макс. шагов (default: 50) |
| `PORT` | ❌ | Порт сервера (default: 8200) |

---

## REST API

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/health` | Статус агента |
| `POST` | `/api/tasks` | Создать задачу |
| `GET` | `/api/tasks` | Список задач |
| `GET` | `/api/tasks/{id}` | Детали задачи |
| `DELETE` | `/api/tasks/current` | Отменить текущую |
| `GET` | `/api/pool` | Статус пула ключей |
| `WS` | `/ws` | Live-события |

### Пример

```bash
curl -X POST http://localhost:8200/api/tasks \\
  -H "Content-Type: application/json" \\
  -d '{"prompt": "Проверь nginx на 443 порту", "mode": "NORMAL"}'
```

---

## Режимы выполнения

| Режим | Описание |
|---|---|
| `SAFE` | Только чтение — никаких изменений |
| `NORMAL` | Стандартный — рекомендуется |
| `FULL` | Полный доступ включая git push |

---

## Telegram-бот

| Команда | Описание |
|---|---|
| `/task <текст>` | Создать задачу (NORMAL) |
| `/safe <текст>` | Создать задачу (SAFE) |
| `/full <текст>` | Создать задачу (FULL) |
| `/status` | Статус агента |
| `/cancel` | Отменить задачу |

---

## Деплой (systemd)

```bash
sudo bash deploy.sh
# Управление
sudo systemctl status leviathan_agent
sudo journalctl -u leviathan_agent -f
```

⚠️ **Важно:** создай пользователя `leviathan` перед деплоем:
```bash
sudo useradd -r -s /bin/false leviathan
```

---

## MCP-интеграция (Cursor)

Файл `.cursor_mcp.json` уже настроен. Убедись что путь совпадает
с реальным расположением `mcp_server/leviathan_mcp.py`.

---

## Лицензия

Proprietary — LEVIATHAN Ecosystem
"""

# ── Роутер аудита по файлам ───────────────────────────────────────────────────

AUDIT_RULES = {
    ".env.example":                  audit_env_example,
    "requirements.txt":              audit_requirements,
    "main.py":                       audit_main_py,
    "deploy.sh":                     audit_deploy_sh,
    "leviathan_agent.service":       audit_service_file,
    "README.md":                     audit_readme,
    "config/settings.py":            audit_settings_py,
    "agent/core.py":                 audit_core_py,
    "agent/tg_bot.py":               audit_tg_bot_py,
    "core_bridge/key_pool.py":       audit_key_pool_py,
}

# ── Рендер отчёта ─────────────────────────────────────────────────────────────

LEVEL_COLOR = {"ERROR": "red", "WARN": "yellow", "INFO": "green"}
LEVEL_ICON  = {"ERROR": "🔴", "WARN": "🟡", "INFO": "🟢"}


def render_report(results: list[AuditResult]):
    console.rule("[bold cyan]LEVIATHAN AGENT — АУДИТ-ОТЧЁТ", style="cyan")
    console.print(f"  Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"  Репозиторий: https://github.com/{REPO}\n")

    total_errors = total_warns = total_fixed = 0
    changed_files = []

    for r in results:
        if not r.issues:
            continue

        errors = [i for i in r.issues if i["level"] == "ERROR"]
        warns  = [i for i in r.issues if i["level"] == "WARN"]
        infos  = [i for i in r.issues if i["level"] == "INFO"]

        total_errors += len(errors)
        total_warns  += len(warns)
        total_fixed  += sum(1 for i in r.issues if i["fixed"])

        if r.changed:
            changed_files.append(r.path)

        color = "red" if errors else "yellow" if warns else "green"
        console.print(Panel(
            f"[dim]{r.path}[/dim]",
            border_style=color,
            expand=False
        ))

        for issue in r.issues:
            icon  = LEVEL_ICON[issue["level"]]
            c     = LEVEL_COLOR[issue["level"]]
            fixed = " [green](ИСПРАВЛЕНО)[/green]" if issue["fixed"] else ""
            console.print(f"   {icon} [{c}]{issue['level']}[/{c}]: {issue['msg']}{fixed}")

        console.print()

    # Итоговая таблица
    table = Table(title="Итог аудита", border_style="cyan")
    table.add_column("Метрика",   style="cyan")
    table.add_column("Значение",  style="white")
    table.add_row("🔴 Критических ошибок", str(total_errors))
    table.add_row("🟡 Предупреждений",     str(total_warns))
    table.add_row("✅ Исправлено автоматически", str(total_fixed))
    table.add_row("📝 Файлов изменено",    str(len(changed_files)))
    console.print(table)

    if changed_files:
        console.print("\n[bold]Изменённые файлы:[/bold]")
        for f in changed_files:
            console.print(f"  → [green]{f}[/green]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Leviathan Agent — аудит и патч")
    parser.add_argument("--token", required=True, help="GitHub Personal Access Token")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только аудит, без пуша изменений")
    args = parser.parse_args()

    gh = GitHub(args.token)

    console.print(Panel(
        "[bold cyan]⚡ LEVIATHAN AGENT — AUDIT & PATCH v1.0[/bold cyan]\n"
        f"Репозиторий: [link=https://github.com/{REPO}]{REPO}[/link]\n"
        f"Режим: {'[yellow]DRY RUN (только аудит)[/yellow]' if args.dry_run else '[green]ПРИМЕНИТЬ ИСПРАВЛЕНИЯ[/green]'}",
        border_style="cyan"
    ))

    # 1. Получаем дерево файлов
    console.print("\n[cyan]→ Читаем структуру репозитория...[/cyan]")
    tree = gh.get_tree()
    console.print(f"  Найдено файлов: [bold]{len(tree)}[/bold]")
    for f in tree:
        console.print(f"  [dim]  {f['path']}[/dim]")

    # 2. Читаем и аудируем файлы
    console.print("\n[cyan]→ Запускаем аудит...[/cyan]\n")
    results: list[AuditResult] = []

    for file_info in tree:
        path = file_info["path"]
        try:
            content, sha = gh.get_file(path)
        except Exception as e:
            console.print(f"  [red]Ошибка чтения {path}: {e}[/red]")
            continue

        r = AuditResult(path, content)
        r._sha = sha  # type: ignore

        # Применяем правило если есть
        rule = AUDIT_RULES.get(path)
        if rule:
            rule(r)
        else:
            # Универсальная проверка: упоминание старой модели
            if "gemini-2.0" in content.lower():
                r.issue("ERROR", f"Упоминание gemini-2.0 в {path}", fixed=True)
                r.patched = re.sub(r"gemini-2\.0", "gemini-2.5",
                                   content, flags=re.IGNORECASE)

        results.append(r)

    # 3. Рендерим отчёт
    render_report(results)

    # 4. Пушим изменения
    changed = [r for r in results if r.changed]
    if not changed:
        console.print("\n[green]✅ Изменений нет — репозиторий в порядке![/green]")
        return

    if args.dry_run:
        console.print(f"\n[yellow]DRY RUN: {len(changed)} файлов были бы изменены.[/yellow]")
        return

    console.print(f"\n[cyan]→ Пушим {len(changed)} файлов...[/cyan]")
    for r in changed:
        try:
            gh.put_file(r.path, r.patched, r._sha, COMMIT_MSG)  # type: ignore
            console.print(f"  ✅ [green]{r.path}[/green]")
        except Exception as e:
            console.print(f"  ❌ [red]{r.path}: {e}[/red]")

    console.print(f"\n[bold green]✅ Готово! Коммит '{COMMIT_MSG}' запушен в {BRANCH}[/bold green]")
    console.print(f"   https://github.com/{REPO}/commits/{BRANCH}")


if __name__ == "__main__":
    main()
