"""
agent/tools.py — инструменты LEVIATHAN AGENT
Каждый инструмент — async функция с описанием для Gemini function calling.
"""
from __future__ import annotations
import asyncio
import logging
import os
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── Константы ──────────────────────────────────────────────────
TIMEOUT     = int(os.environ.get("TOOL_TIMEOUT_SEC", "300"))  # 5 минут по умолчанию
MAX_OUTPUT  = 8000   # символов — обрезаем длинный вывод
MAX_FILE_KB = int(os.environ.get("MAX_FILE_SIZE_KB", "100"))

# Команды требующие подтверждения в режиме NORMAL
DANGEROUS_PATTERNS = [
    "rm -rf", "DROP TABLE", "DROP DATABASE",
    "systemctl stop", "systemctl disable",
    "mkfs", "dd if=", "> /dev/",
]


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n\n... [обрезано, всего {len(text)} символов] ...\n\n" + text[-half:]


def is_dangerous(cmd: str) -> bool:
    return any(p in cmd for p in DANGEROUS_PATTERNS)


# ══════════════════════════════════════════════════════════════
# BASH
# ══════════════════════════════════════════════════════════════

async def bash_tool(cmd: str, workdir: str = "/var/www/voicestudio") -> dict:
    """
    Выполняет bash команду на сервере.
    Возвращает stdout, stderr, returncode.
    """
    logger.info("bash: %s (cwd=%s)", cmd[:80], workdir)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir if os.path.exists(workdir) else "/root",
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TIMEOUT
        )
        result = {
            "stdout": _truncate(stdout.decode(errors="replace")),
            "stderr": _truncate(stderr.decode(errors="replace")),
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
        }
        logger.info("bash: rc=%d stdout=%d chars", proc.returncode, len(result["stdout"]))
        return result
    except asyncio.TimeoutError:
        return {"error": f"Таймаут {TIMEOUT}с", "ok": False}
    except Exception as e:
        return {"error": str(e), "ok": False}


# ══════════════════════════════════════════════════════════════
# ФАЙЛЫ
# ══════════════════════════════════════════════════════════════

async def read_file(path: str) -> dict:
    """Читает файл. Лимит MAX_FILE_SIZE_KB КБ."""
    logger.info("read_file: %s", path)
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Файл не найден: {path}", "ok": False}
        size_kb = p.stat().st_size / 1024
        if size_kb > MAX_FILE_KB:
            return {
                "error": f"Файл слишком большой: {size_kb:.0f}KB (лимит {MAX_FILE_KB}KB)",
                "ok": False,
            }
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "path": str(p.resolve()), "size_kb": round(size_kb, 1), "ok": True}
    except Exception as e:
        return {"error": str(e), "ok": False}


async def write_file(path: str, content: str) -> dict:
    """Записывает файл. Создаёт директории если нужно."""
    logger.info("write_file: %s (%d bytes)", path, len(content))
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p.resolve()), "size_bytes": len(content.encode())}
    except Exception as e:
        return {"error": str(e), "ok": False}


async def list_dir(path: str, pattern: str = "*") -> dict:
    """Список файлов в директории."""
    logger.info("list_dir: %s", path)
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Директория не найдена: {path}", "ok": False}
        items = []
        for item in sorted(p.iterdir()):
            items.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size_kb": round(item.stat().st_size / 1024, 1) if item.is_file() else None,
            })
        return {"items": items, "path": str(p.resolve()), "count": len(items), "ok": True}
    except Exception as e:
        return {"error": str(e), "ok": False}


async def search_in_files(pattern: str, directory: str, extensions: str = "py,js,html") -> dict:
    """grep -r паттерн в директории."""
    logger.info("search: '%s' in %s", pattern, directory)
    exts = "|".join(f"*.{e.strip()}" for e in extensions.split(","))
    cmd = f'grep -r --include="{exts}" -n "{pattern}" "{directory}" 2>/dev/null | head -50'
    result = await bash_tool(cmd, workdir="/root")
    return result


# ══════════════════════════════════════════════════════════════
# GIT
# ══════════════════════════════════════════════════════════════

async def git_commit_push(
    repo_path: str,
    message: str,
    github_token: str = "",
    branch: str = "main",
) -> dict:
    """
    git add -A → commit → push.
    Автоматически настраивает remote с токеном.
    """
    logger.info("git: commit '%s' в %s", message[:50], repo_path)
    token = github_token or os.environ.get("GITHUB_TOKEN", "")

    # Получаем remote URL
    get_remote = await bash_tool("git remote get-url origin", workdir=repo_path)
    remote_url = get_remote.get("stdout", "").strip()

    # Вставляем токен если нужно
    if token and "github.com" in remote_url and "@" not in remote_url:
        remote_url = remote_url.replace("https://", f"https://{token}@")
        await bash_tool(f"git remote set-url origin {remote_url}", workdir=repo_path)

    # Коммит
    cmds = [
        "git config user.email 'leviathan@agent.ai'",
        "git config user.name 'LEVIATHAN AGENT'",
        "git add -A",
        f'git commit -m "{message}"',
        f"git push origin {branch}",
    ]
    for cmd in cmds:
        result = await bash_tool(cmd, workdir=repo_path)
        if not result.get("ok") and "nothing to commit" not in result.get("stdout", "") + result.get("stderr", ""):
            logger.warning("git: %s → %s", cmd, result.get("stderr", "")[:100])

    # Верификация: проверяем что push реально дошёл
    verify = await bash_tool(f"git log origin/{branch}..HEAD --oneline", workdir=repo_path)
    unpushed = verify.get("stdout", "").strip()
    if unpushed:
        return {"ok": False, "error": f"Push не удался, не запушено {len(unpushed.splitlines())} коммит(ов). Проверь GITHUB_TOKEN.", "repo": repo_path}

    # Убираем токен из URL после push
    if token and "@" in remote_url:
        clean_url = remote_url.replace(f"{token}@", "")
        await bash_tool(f"git remote set-url origin {clean_url}", workdir=repo_path)

    return {"ok": True, "message": message, "repo": repo_path, "pushed": True}


# ══════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════

async def http_get(url: str, headers: dict | None = None) -> dict:
    """HTTP GET запрос."""
    logger.info("http_get: %s", url)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=headers or {})
            return {
                "status": r.status_code,
                "ok": r.is_success,
                "text": _truncate(r.text),
                "headers": dict(r.headers),
            }
    except Exception as e:
        return {"error": str(e), "ok": False}


async def http_post(url: str, body: dict, headers: dict | None = None) -> dict:
    """HTTP POST запрос с JSON телом."""
    logger.info("http_post: %s", url)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=body, headers=headers or {})
            return {
                "status": r.status_code,
                "ok": r.is_success,
                "text": _truncate(r.text),
            }
    except Exception as e:
        return {"error": str(e), "ok": False}


# ══════════════════════════════════════════════════════════════
# РЕЕСТР ИНСТРУМЕНТОВ для Gemini function calling
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
# CLAUDE THINK — аналитика через Claude
# ══════════════════════════════════════════════════════════════

async def claude_think(
    prompt:       str,
    context:      str = "",
    use_thinking: bool = False,
) -> dict:
    """
    Вызывает Claude для аналитических, архитектурных задач и code review.
    Работает в режимах GEMINI_THINK_CLAUDE и AUTO.
    """
    try:
        from core_bridge.claude_adapter import get_claude_adapter
        adapter = get_claude_adapter()
        result  = await adapter.call_tool(
            task_description = prompt,
            context          = context,
            use_thinking     = use_thinking,
        )
        return {"ok": True, "result": result, "provider": "claude"}
    except Exception as e:
        logger.error("claude_think: %s", e)
        return {"ok": False, "error": str(e), "provider": "claude"}


TOOLS_REGISTRY = {
    "bash_tool":         bash_tool,
    "read_file":         read_file,
    "write_file":        write_file,
    "list_dir":          list_dir,
    "search_in_files":   search_in_files,
    "git_commit_push":   git_commit_push,
    "claude_think":    claude_think,
    "http_get":          http_get,
    "http_post":         http_post,
}

# Описания для Gemini (function declarations)
GEMINI_TOOLS = [
    {
        "name": "bash_tool",
        "description": "Выполнить bash команду на сервере. Используй для запуска скриптов, установки пакетов, управления сервисами.",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd":     {"type": "string", "description": "Bash команда для выполнения"},
                "workdir": {"type": "string", "description": "Рабочая директория (по умолчанию /var/www/voicestudio)"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "read_file",
        "description": "Прочитать содержимое файла. Лимит 100KB.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Абсолютный путь к файлу"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Записать содержимое в файл. Создаёт директории автоматически.",
        "parameters": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Абсолютный путь к файлу"},
                "content": {"type": "string", "description": "Содержимое файла"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "Список файлов в директории.",
        "parameters": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Путь к директории"},
                "pattern": {"type": "string", "description": "Glob паттерн (по умолчанию *)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_in_files",
        "description": "Поиск паттерна в файлах (grep -r).",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern":    {"type": "string", "description": "Строка для поиска"},
                "directory":  {"type": "string", "description": "Директория для поиска"},
                "extensions": {"type": "string", "description": "Расширения файлов через запятую (py,js,html)"},
            },
            "required": ["pattern", "directory"],
        },
    },
    {
        "name": "git_commit_push",
        "description": "Сделать git commit и push на GitHub.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Путь к репозиторию"},
                "message":   {"type": "string", "description": "Сообщение коммита"},
                "branch":    {"type": "string", "description": "Ветка (по умолчанию main)"},
            },
            "required": ["repo_path", "message"],
        },
    },
    {
        "name": "http_get",
        "description": "HTTP GET запрос к любому URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL для запроса"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "http_post",
        "description": "HTTP POST запрос с JSON телом.",
        "parameters": {
            "type": "object",
            "properties": {
                "url":  {"type": "string", "description": "URL для запроса"},
                "body": {"type": "object", "description": "JSON тело запроса"},
            },
            "required": ["url", "body"],
        },
    },
    {
        "name": "claude_think",
        "description": "Вызвать Claude для аналитики, архитектуры, code review, декомпозиции, сравнения подходов. Используй для задач где нужно думать, а не исполнять.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt":       {"type": "string",  "description": "Задача или вопрос для Claude"},
                "context":      {"type": "string",  "description": "Доп. контекст: код, конфиг, логи"},
                "use_thinking": {"type": "boolean", "description": "Extended thinking для сложных задач"},
            },
            "required": ["prompt"],
        },
    },
]


# ── ArbitrCockpit инструменты (опционально) ────────────────
try:
    from agent.tools_arbitr import register_arbitr_tools
    register_arbitr_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
except ImportError:
    pass  # ArbitrCockpit не установлен — работаем без него

# НОВЫЙ КОД: Инструменты доставки
from agent.tools_delivery import register_delivery_tools
register_delivery_tools(TOOLS_REGISTRY, GEMINI_TOOLS)



# ── Extra tools (v3.2) ────────────────────────────────────────
try:
    from agent.tools_extra import register_extra_tools
    register_extra_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
except ImportError as _e:
    import logging
    logging.getLogger("tools").warning("tools_extra not available: %s", _e)

# ── File tools: send_file_to_tg, write_and_send_tg ────────────
try:
    from agent.tools_file import TOOL_DEFINITIONS as _FD, TOOL_HANDLERS as _FH
    for _td in _FD:
        GEMINI_TOOLS.append(_td)
    TOOLS_REGISTRY.update(_FH)
except ImportError as _e:
    import logging
    logging.getLogger("tools").warning("tools_file not available: %s", _e)

# ── Diet Platform «Пухляш» tools ───────────────────────────
try:
    from agent.tools_diet_platform import register_diet_tools
    register_diet_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
except ImportError as _e:
    import logging
    logging.getLogger("tools").warning("tools_diet_platform not available: %s", _e)

# ── Дедупликация: убираем дублирующиеся имена инструментов ──
_seen_tools: set[str] = set()
_deduped: list = []
for _t in GEMINI_TOOLS:
    _name = _t.get("name") or _t.get("function", {}).get("name", "")
    if _name and _name not in _seen_tools:
        _seen_tools.add(_name)
        _deduped.append(_t)
GEMINI_TOOLS[:] = _deduped
