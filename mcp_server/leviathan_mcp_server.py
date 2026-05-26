"""
mcp_server/leviathan_mcp_server.py — LEVIATHAN MCP Server v1.0
═══════════════════════════════════════════════════════════════════
FastMCP + Streamable HTTP на порту 8300.
Даёт Claude прямой доступ к серверу: файлы, bash, git, systemd.

Установка:
    pip install "mcp[cli]" pydantic httpx

Запуск:
    python leviathan_mcp_server.py

Порт:  8300
Auth:  Bearer token (LEV_MCP_TOKEN в .env)

Подключение в claude.ai → Settings → MCP:
    URL:   http://78.17.24.96:8300/mcp
    Token: <LEV_MCP_TOKEN>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ── Конфигурация ──────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("leviathan_mcp")

MCP_TOKEN      = os.getenv("LEV_MCP_TOKEN", "leviathan-mcp-secret-change-me")
AGENT_DIR      = os.getenv("AGENT_DIR", "/opt/leviathan_engine/agent_service")
MAX_FILE_SIZE  = 100 * 1024   # 100 KB
MAX_CMD_OUTPUT = 8_000        # символов в выводе команды

# ── Инициализация FastMCP ──────────────────────────────────────────────────────

mcp = FastMCP(
    "leviathan_mcp",
    host="0.0.0.0",
    instructions=(
        "MCP сервер LEVIATHAN AGENT. Даёт прямой доступ к серверу 78.17.24.96: "
        "чтение/запись файлов, выполнение bash, git, управление systemd сервисами. "
        "Все пути относительно AGENT_DIR=/opt/leviathan_engine/agent_service если не указано иное."
    ),
)

# ── Вспомогательные функции ───────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    """Преобразует относительный путь в абсолютный относительно AGENT_DIR."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(AGENT_DIR) / p
    return p.resolve()

def _safe_path(path: str) -> tuple[Path, str | None]:
    """Возвращает (Path, error). Блокирует выход за пределы сервера."""
    try:
        p = _resolve_path(path)
        return p, None
    except Exception as e:
        return Path(), f"Ошибка пути: {e}"

async def _run_cmd(
    cmd: str | list[str],
    cwd: str = AGENT_DIR,
    timeout: int = 30,
) -> dict:
    """Выполняет команду асинхронно, возвращает {ok, stdout, stderr, returncode}."""
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "stdout": "", "stderr": f"Timeout {timeout}s", "returncode": -1}

    out = stdout.decode(errors="replace")[:MAX_CMD_OUTPUT]
    err = stderr.decode(errors="replace")[:2000]
    return {
        "ok":         proc.returncode == 0,
        "stdout":     out,
        "stderr":     err,
        "returncode": proc.returncode,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ИНСТРУМЕНТЫ
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. lev_read_file ──────────────────────────────────────────────────────────

class ReadFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path:       str           = Field(..., description="Путь к файлу. Относительный — от AGENT_DIR.")
    max_chars:  Optional[int] = Field(default=None, description="Обрезать до N символов (по умолчанию без обрезки до 100KB)")

@mcp.tool(
    name="lev_read_file",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def lev_read_file(params: ReadFileInput) -> str:
    """Читает файл с сервера и возвращает его содержимое.

    Args:
        params.path:      путь к файлу (относительный или абсолютный)
        params.max_chars: обрезать вывод до N символов

    Returns:
        str: содержимое файла или сообщение об ошибке
    """
    p, err = _safe_path(params.path)
    if err:
        return f"Error: {err}"
    if not p.exists():
        return f"Error: Файл не найден: {p}"
    if p.stat().st_size > MAX_FILE_SIZE:
        return f"Error: Файл слишком большой ({p.stat().st_size // 1024}KB > 100KB). Используй lev_bash с head/tail."
    try:
        content = p.read_text(errors="replace")
        if params.max_chars:
            content = content[:params.max_chars]
        logger.info("read_file: %s (%d chars)", p, len(content))
        return content
    except Exception as e:
        return f"Error: {e}"


# ── 2. lev_write_file ─────────────────────────────────────────────────────────

class WriteFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path:    str  = Field(..., description="Путь к файлу. Создаст директории если нужно.")
    content: str  = Field(..., description="Содержимое файла (полная замена)")
    backup:  bool = Field(default=True, description="Создать .bak перед записью")

@mcp.tool(
    name="lev_write_file",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}
)
async def lev_write_file(params: WriteFileInput) -> str:
    """Записывает файл на сервер (полная замена содержимого).

    ВНИМАНИЕ: перезаписывает файл целиком. Для частичных изменений используй lev_patch.

    Args:
        params.path:    путь к файлу
        params.content: новое содержимое
        params.backup:  создать .bak (по умолчанию True)

    Returns:
        str: JSON с результатом {ok, path, backup_path, size}
    """
    p, err = _safe_path(params.path)
    if err:
        return f"Error: {err}"

    backup_path = ""
    if params.backup and p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_bytes(p.read_bytes())
        backup_path = str(bak)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(params.content, encoding="utf-8")
        logger.info("write_file: %s (%d chars)", p, len(params.content))
        return json.dumps({
            "ok":          True,
            "path":        str(p),
            "backup_path": backup_path,
            "size":        len(params.content),
        }, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"


# ── 3. lev_patch ─────────────────────────────────────────────────────────────

class PatchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path:    str = Field(..., description="Путь к файлу")
    old_str: str = Field(..., description="Строка для замены (должна встречаться РОВНО 1 раз)")
    new_str: str = Field(default="", description="Новая строка (пустая = удалить old_str)")

@mcp.tool(
    name="lev_patch",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def lev_patch(params: PatchInput) -> str:
    """Заменяет строку в файле (аналог str_replace). Безопаснее чем lev_write_file.

    Требует чтобы old_str встречался РОВНО 1 раз в файле.
    Автоматически создаёт .bak перед изменением.

    Args:
        params.path:    путь к файлу
        params.old_str: уникальная строка для замены
        params.new_str: строка-замена (или пусто для удаления)

    Returns:
        str: JSON {ok, path, occurrences, changed}
    """
    p, err = _safe_path(params.path)
    if err:
        return f"Error: {err}"
    if not p.exists():
        return f"Error: Файл не найден: {p}"

    content = p.read_text(errors="replace")
    count   = content.count(params.old_str)

    if count == 0:
        return json.dumps({"ok": False, "error": "old_str не найден в файле", "occurrences": 0})
    if count > 1:
        return json.dumps({
            "ok": False,
            "error": f"old_str встречается {count} раз — нужна более уникальная строка",
            "occurrences": count,
        })

    # Бэкап
    p.with_suffix(p.suffix + ".bak").write_bytes(p.read_bytes())
    new_content = content.replace(params.old_str, params.new_str, 1)
    p.write_text(new_content, encoding="utf-8")

    logger.info("patch: %s (-%d +%d chars)", p, len(params.old_str), len(params.new_str))
    return json.dumps({"ok": True, "path": str(p), "occurrences": 1, "changed": True})


# ── 4. lev_list_dir ───────────────────────────────────────────────────────────

class ListDirInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path:       str           = Field(default=".", description="Директория для листинга")
    recursive:  bool          = Field(default=False, description="Рекурсивный листинг")
    pattern:    Optional[str] = Field(default=None, description="Фильтр по шаблону (glob), например '*.py'")

@mcp.tool(
    name="lev_list_dir",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def lev_list_dir(params: ListDirInput) -> str:
    """Листинг директории сервера.

    Args:
        params.path:      директория (по умолчанию AGENT_DIR)
        params.recursive: рекурсивно (по умолчанию False)
        params.pattern:   glob-фильтр (например '*.py')

    Returns:
        str: JSON список файлов [{name, size, modified, is_dir}]
    """
    p, err = _safe_path(params.path)
    if err:
        return f"Error: {err}"
    if not p.is_dir():
        return f"Error: Не директория: {p}"

    try:
        glob_fn = p.rglob if params.recursive else p.glob
        pattern = params.pattern or "*"
        items   = []
        for entry in sorted(glob_fn(pattern)):
            try:
                stat = entry.stat()
                items.append({
                    "name":     str(entry.relative_to(p)),
                    "size":     stat.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                    "is_dir":   entry.is_dir(),
                })
            except Exception:
                pass
        return json.dumps({"path": str(p), "count": len(items), "items": items}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error: {e}"


# ── 5. lev_bash ───────────────────────────────────────────────────────────────

# Заблокированные команды
BLOCKED_CMDS = ["rm -rf /", "mkfs", "dd if=/dev/zero", ":(){:|:&};:"]

class BashInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    cmd:     str           = Field(..., description="Bash команда для выполнения", max_length=2000)
    cwd:     Optional[str] = Field(default=None, description="Рабочая директория (по умолчанию AGENT_DIR)")
    timeout: int           = Field(default=30, description="Таймаут в секундах", ge=1, le=300)

@mcp.tool(
    name="lev_bash",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}
)
async def lev_bash(params: BashInput) -> str:
    """Выполняет bash-команду на сервере.

    ВНИМАНИЕ: деструктивный инструмент. Перед изменяющими командами всегда
    читай файлы через lev_read_file и делай diff через lev_bash с git diff.

    Args:
        params.cmd:     команда для выполнения
        params.cwd:     рабочая директория (по умолчанию AGENT_DIR)
        params.timeout: таймаут в секундах (max 300)

    Returns:
        str: JSON {ok, stdout, stderr, returncode, duration_ms}
    """
    for blocked in BLOCKED_CMDS:
        if blocked in params.cmd:
            return json.dumps({"ok": False, "error": f"Команда заблокирована: {blocked}"})

    cwd = str(_resolve_path(params.cwd)) if params.cwd else AGENT_DIR
    t0  = time.perf_counter()
    result = await _run_cmd(params.cmd, cwd=cwd, timeout=params.timeout)
    result["duration_ms"] = round((time.perf_counter() - t0) * 1000)
    result["cmd"] = params.cmd[:200]

    logger.info("bash: [%d] %s", result["returncode"], params.cmd[:80])
    return json.dumps(result, ensure_ascii=False)


# ── 6. lev_git ────────────────────────────────────────────────────────────────

class GitInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action:  str           = Field(..., description="Действие: status|diff|pull|add|commit|push|log|smart")
    message: Optional[str] = Field(default=None, description="Сообщение коммита (для commit и smart)")
    files:   Optional[str] = Field(default=".", description="Файлы для git add (по умолчанию '.')")

@mcp.tool(
    name="lev_git",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def lev_git(params: GitInput) -> str:
    """Git операции в репозитории агента.

    Actions:
        status  — git status
        diff    — git diff --stat
        pull    — git pull --rebase origin main
        add     — git add {files}
        commit  — git commit -m "{message}"
        push    — git push origin main
        log     — последние 10 коммитов
        smart   — pull → add -A → commit → push (одна команда)

    Args:
        params.action:  действие (обязательно)
        params.message: сообщение для commit/smart
        params.files:   файлы для add

    Returns:
        str: JSON {ok, output, action}
    """
    action = params.action.lower().strip()

    cmd_map = {
        "status": "git status --short",
        "diff":   "git diff --stat",
        "pull":   "git pull --rebase origin main",
        "add":    f"git add {params.files or '.'}",
        "push":   "git push origin main",
        "log":    "git log --oneline -10",
    }

    if action == "commit":
        if not params.message:
            return json.dumps({"ok": False, "error": "message обязателен для commit"})
        cmd = f'git commit -m "{params.message}"'
    elif action == "smart":
        msg = params.message or "chore: auto-commit by leviathan-mcp"
        results = []
        for step_cmd in [
            "git pull --rebase origin main",
            "git add -A",
            f'git commit -m "{msg}"',
            "git push origin main",
        ]:
            r = await _run_cmd(step_cmd, cwd=AGENT_DIR)
            results.append({"cmd": step_cmd, "ok": r["ok"], "out": r["stdout"][:200]})
            if not r["ok"] and "nothing to commit" not in r["stdout"] + r["stderr"]:
                return json.dumps({"ok": False, "action": "smart", "failed_at": step_cmd, "steps": results})
        return json.dumps({"ok": True, "action": "smart", "steps": results})
    elif action in cmd_map:
        cmd = cmd_map[action]
    else:
        return json.dumps({"ok": False, "error": f"Неизвестный action: {action}. Допустимые: {list(cmd_map.keys()) + ['commit', 'smart']}"})

    result = await _run_cmd(cmd, cwd=AGENT_DIR)
    return json.dumps({
        "ok":     result["ok"],
        "action": action,
        "output": (result["stdout"] + result["stderr"])[:3000],
    }, ensure_ascii=False)


# ── 7. lev_systemctl ─────────────────────────────────────────────────────────

KNOWN_SERVICES = [
    "leviathan_agent", "arbitr_cockpit", "voicestudio",
    "kinovibe", "ai_outreach", "orionyx", "nginx",
]

class SystemctlInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action:  str           = Field(..., description="Действие: status|start|stop|restart|logs")
    service: str           = Field(..., description=f"Сервис. Известные: {KNOWN_SERVICES}")
    lines:   Optional[int] = Field(default=50, description="Строк логов (для action=logs)")

@mcp.tool(
    name="lev_systemctl",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}
)
async def lev_systemctl(params: SystemctlInput) -> str:
    """Управление systemd сервисами.

    Actions: status | start | stop | restart | logs

    Args:
        params.action:  действие
        params.service: имя сервиса
        params.lines:   кол-во строк для logs (по умолчанию 50)

    Returns:
        str: JSON {ok, action, service, output}
    """
    action  = params.action.lower().strip()
    service = params.service.strip()

    if action == "logs":
        cmd = f"journalctl -u {service} -n {params.lines or 50} --no-pager"
    elif action in ("status", "start", "stop", "restart"):
        cmd = f"systemctl {action} {service}"
    else:
        return json.dumps({"ok": False, "error": f"Неизвестный action: {action}"})

    result = await _run_cmd(cmd, timeout=60)
    return json.dumps({
        "ok":      result["ok"],
        "action":  action,
        "service": service,
        "output":  (result["stdout"] + result["stderr"])[:4000],
    }, ensure_ascii=False)


# ── 8. lev_health ─────────────────────────────────────────────────────────────

@mcp.tool(
    name="lev_health",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def lev_health() -> str:
    """Проверяет статус всех сервисов экосистемы LEVIATHAN.

    Проверяет:
        - HTTP /health каждого сервиса
        - systemd is-active статус
        - Leviathan Agent pool (ключи Gemini)

    Returns:
        str: JSON {healthy, total, services: {name: {ok, http, systemd}}}
    """
    services = {
        "leviathan_agent": 8200, "arbitr_cockpit": 8095,
        "voicestudio": 8120,     "kinovibe": 8110,
        "ai_outreach": 8000,     "orionyx": 8005,
    }
    results = {}

    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, port in services.items():
            s = {"name": name, "ok": False}
            try:
                r = await client.get(f"http://localhost:{port}/health")
                s["http"] = r.status_code
                s["ok"]   = r.status_code < 400
                try:
                    s["data"] = r.json()
                except Exception:
                    pass
            except Exception as e:
                s["http_error"] = str(e)[:80]

            rc, _ = (await _run_cmd(f"systemctl is-active {name}", timeout=3)).values()
            out = (await _run_cmd(f"systemctl is-active {name}", timeout=3))
            s["systemd"] = out["stdout"].strip() if out["ok"] else "inactive"
            results[name] = s

    healthy = sum(1 for s in results.values() if s.get("ok"))
    return json.dumps({
        "ok":      healthy == len(results),
        "healthy": healthy,
        "total":   len(results),
        "services": results,
    }, ensure_ascii=False, indent=2)


# ── 9. lev_agent_task ────────────────────────────────────────────────────────

class AgentTaskInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt:     str           = Field(..., description="Задача для Leviathan Agent", min_length=5)
    mode:       str           = Field(default="NORMAL", description="SAFE | NORMAL | FULL")
    model_mode: Optional[str] = Field(default=None, description="GEMINI_ONLY | CLAUDE_ONLY | AUTO | ...")

@mcp.tool(
    name="lev_agent_task",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
)
async def lev_agent_task(params: AgentTaskInput) -> str:
    """Отправляет задачу в Leviathan Agent через REST API (localhost:8200).

    Используй когда нужно задействовать полный pipeline агента
    (Gemini FC loop, Groq fallback, KnowledgeBase, Telegram уведомления).

    Args:
        params.prompt:     текст задачи
        params.mode:       SAFE | NORMAL | FULL
        params.model_mode: GEMINI_ONLY | CLAUDE_ONLY | AUTO | ...

    Returns:
        str: JSON {task_id, status} и результат после завершения
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(
                "http://localhost:8200/api/tasks",
                json={"prompt": params.prompt, "mode": params.mode, "model_mode": params.model_mode},
            )
            return json.dumps(r.json(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})


# ── 10. lev_find ─────────────────────────────────────────────────────────────

class FindInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    pattern:    str           = Field(..., description="Строка для поиска (grep)")
    path:       Optional[str] = Field(default=".", description="Директория поиска")
    file_glob:  Optional[str] = Field(default="*.py", description="Маска файлов (по умолчанию *.py)")
    max_results: int          = Field(default=30, description="Максимум совпадений", ge=1, le=200)

@mcp.tool(
    name="lev_find",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
)
async def lev_find(params: FindInput) -> str:
    """Ищет строку в файлах проекта (grep -rn).

    Args:
        params.pattern:    строка для поиска
        params.path:       директория (по умолчанию AGENT_DIR)
        params.file_glob:  маска файлов (по умолчанию *.py)
        params.max_results: макс. совпадений

    Returns:
        str: JSON {ok, count, matches: [{file, line, text}]}
    """
    search_path = str(_resolve_path(params.path or "."))
    cmd = f'grep -rn --include="{params.file_glob}" {json.dumps(params.pattern)} {search_path}'
    result = await _run_cmd(cmd, timeout=15)

    lines   = result["stdout"].splitlines()[:params.max_results]
    matches = []
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            matches.append({"file": parts[0], "line": parts[1], "text": parts[2].strip()})

    return json.dumps({
        "ok":      True,
        "pattern": params.pattern,
        "count":   len(matches),
        "matches": matches,
    }, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES — статические данные
# ══════════════════════════════════════════════════════════════════════════════

@mcp.resource("leviathan://config/env-example")
async def get_env_example() -> str:
    """Шаблон .env.example для Leviathan Agent."""
    p = Path(AGENT_DIR) / ".env.example"
    return p.read_text() if p.exists() else "Файл не найден"

@mcp.resource("leviathan://config/requirements")
async def get_requirements() -> str:
    """requirements.txt Leviathan Agent."""
    p = Path(AGENT_DIR) / "requirements.txt"
    return p.read_text() if p.exists() else "Файл не найден"

@mcp.resource("leviathan://status/services")
async def get_services_list() -> str:
    """Список всех сервисов экосистемы с портами."""
    return json.dumps({
        "services": {
            "leviathan_agent": {"port": 8200, "path": AGENT_DIR},
            "arbitr_cockpit":  {"port": 8095, "path": "/opt/arbitr_cockpit"},
            "voicestudio":     {"port": 8120, "path": "/var/www/voicestudio"},
            "kinovibe":        {"port": 8110, "path": "/var/www/kinovibe"},
            "ai_outreach":     {"port": 8000, "path": "/opt/ai_outreach"},
            "orionyx":         {"port": 8005, "path": "/opt/orionyx"},
        }
    }, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    port = int(os.getenv("MCP_PORT", 8300))
    print(f"[INFO] Starting FastMCP Server on port {port}")
    # Передаем сам объект sse_app напрямую без factory-строки
    uvicorn.run(mcp.streamable_http_app, host="0.0.0.0", port=port)
