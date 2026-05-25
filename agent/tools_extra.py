"""
agent/tools_extra.py — Дополнительные инструменты Leviathan Agent v3.2

Инструменты:
  token_tracker     — статистика токенов
  health_monitor    — проверка всех сервисов
  git_smart         — умный git: pull → diff → commit → push
  error_detector    — парсинг последней ошибки из journalctl
  api_tester        — тест HTTP endpoint
  multi_send        — отправить несколько файлов в TG
  project_context   — загрузить контекст проекта из KB
  onboard_project   — аудит нового проекта из архива
  diff_review       — показать git diff перед коммитом
  session_summary   — сводка дня и отправка в TG
"""
import logging
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tools_extra")

# Инжектируется из main.py
_bot_instance = None
_admin_chat_id = None
_kb_instance = None
_db_path = None


def inject_extra_deps(bot, chat_id, kb, db_path):
    global _bot_instance, _admin_chat_id, _kb_instance, _db_path
    _bot_instance = bot
    _admin_chat_id = chat_id
    _kb_instance = kb
    _db_path = db_path


SERVICES = [
    {"name": "Leviathan Agent",  "port": 8200, "path": "/health"},
    {"name": "ArbitrCockpit",    "port": 8095, "path": "/health"},
    {"name": "VoiceStudio",      "port": 8120, "path": "/health"},
    {"name": "KinoVibe",         "port": 8110, "path": "/"},
    {"name": "AI Outreach",      "port": 8000, "path": "/"},
    {"name": "Orionyx",          "port": 8005, "path": "/"},
]


# ── 1. token_tracker ──────────────────────────────────────────

async def get_token_stats(days: int = 7) -> dict:
    """Статистика токенов за N дней."""
    try:
        from db.token_stats import get_stats
        stats = await get_stats(days=days)
        today = stats.get("today", {})
        summary = (
            f"📊 Токены за сегодня: "
            f"↑{today.get('tin',0)} + ↓{today.get('tout',0)} = "
            f"{(today.get('tin',0) or 0) + (today.get('tout',0) or 0)}\n"
        )
        by_day = stats.get("by_day", [])
        if by_day:
            summary += "По дням:\n"
            for d in by_day[:7]:
                summary += (
                    f"  {d['date']}: {d['calls']} вызов(а), "
                    f"{(d['tin'] or 0)+(d['tout'] or 0)} токенов\n"
                )
        return {"ok": True, "summary": summary, "data": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 2. health_monitor ────────────────────────────────────────

async def health_monitor() -> dict:
    """Проверить все сервисы одной командой."""
    import aiohttp
    import asyncio

    results = []
    async def check(svc):
        url = f"http://localhost:{svc['port']}{svc['path']}"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3)
            ) as session:
                async with session.get(url) as r:
                    status = "✅" if r.status < 400 else "⚠️"
                    results.append(f"{status} {svc['name']} :{svc['port']} → {r.status}")
        except Exception as e:
            results.append(f"❌ {svc['name']} :{svc['port']} → недоступен")

    import asyncio
    await asyncio.gather(*[check(s) for s in SERVICES])
    report = "\n".join(results)
    return {"ok": True, "report": report, "services": results}


# ── 3. git_smart ──────────────────────────────────────────────

async def git_smart(
    workdir: str,
    message: str,
    push: bool = True,
    show_diff: bool = False,
) -> dict:
    """Умный git: pull → diff → commit → push."""
    def run(cmd):
        r = subprocess.run(
            cmd, shell=True, cwd=workdir,
            capture_output=True, text=True
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode

    out, err, code = run("git pull origin main 2>&1")
    if code != 0 and "Already up to date" not in out:
        return {"ok": False, "step": "pull", "error": err or out}

    diff_out, _, _ = run("git diff --stat HEAD")

    out, err, code = run("git add -A")
    if code != 0:
        return {"ok": False, "step": "add", "error": err}

    out, err, code = run(f'git commit -m "{message}"')
    if code != 0:
        if "nothing to commit" in out or "nothing to commit" in err:
            return {"ok": True, "committed": False, "msg": "Нечего коммитить"}
        return {"ok": False, "step": "commit", "error": err}

    if push:
        out, err, code = run("git push origin main")
        if code != 0:
            return {"ok": False, "step": "push", "error": err}

    return {
        "ok": True,
        "committed": True,
        "pushed": push,
        "diff_stat": diff_out,
        "message": message,
    }


# ── 4. error_detector ────────────────────────────────────────

async def error_detector(service: str = "leviathan_agent", lines: int = 50) -> dict:
    """Найти последнюю ошибку в journalctl."""
    cmd = f"journalctl -u {service} -n {lines} --no-pager"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    log = r.stdout

    error_lines = []
    for line in log.splitlines():
        low = line.lower()
        if any(kw in low for kw in ["error", "exception", "traceback",
                                     "failed", "critical", "fatal"]):
            error_lines.append(line)

    if not error_lines:
        return {"ok": True, "errors_found": False,
                "msg": "Ошибок не найдено в последних логах"}

    return {
        "ok": True,
        "errors_found": True,
        "errors": error_lines[-10:],
        "raw_tail": log.splitlines()[-20:],
    }


# ── 5. api_tester ────────────────────────────────────────────

async def api_tester(
    url: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> dict:
    """Тест HTTP endpoint."""
    import aiohttp
    import json
    try:
        async with aiohttp.ClientSession() as session:
            kwargs = {
                "headers": headers or {},
                "timeout": aiohttp.ClientTimeout(total=10),
            }
            if payload:
                kwargs["json"] = payload

            method_fn = getattr(session, method.lower())
            async with method_fn(url, **kwargs) as r:
                try:
                    body = await r.json()
                except Exception:
                    body = await r.text()

                return {
                    "ok": True,
                    "status": r.status,
                    "body": body,
                    "headers": dict(r.headers),
                }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 6. multi_send ────────────────────────────────────────────

async def multi_send(paths: list[str], caption: str = "") -> dict:
    """Отправить несколько файлов в TG."""
    if _bot_instance is None:
        return {"ok": False, "error": "Бот не инициализирован"}

    sent = []
    failed = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            failed.append({"path": path, "error": "не найден"})
            continue
        try:
            from aiogram.types import FSInputFile
            doc = FSInputFile(str(p), filename=p.name)
            await _bot_instance.send_document(
                chat_id=_admin_chat_id,
                document=doc,
                caption=f"📎 {p.name}" if not caption else caption,
            )
            sent.append(str(p))
        except Exception as e:
            failed.append({"path": path, "error": str(e)})

    return {"ok": len(sent) > 0, "sent": sent, "failed": failed}


# ── 7. project_context ───────────────────────────────────────

async def project_context(project_name: str) -> dict:
    """Загрузить контекст проекта из KB."""
    if _kb_instance is None:
        return {"ok": False, "error": "KB не инициализирована"}
    results = await _kb_instance.search(project_name, limit=10)
    files = await _kb_instance.find_file(project_name)
    return {
        "ok": True,
        "project": project_name,
        "entries": results,
        "files": files,
    }


# ── 8. onboard_project ───────────────────────────────────────

async def onboard_project(zip_path: str, project_name: str = "") -> dict:
    """
    Аудит нового проекта из ZIP архива.
    Распаковывает → изучает структуру → сохраняет в KB.
    """
    zp = Path(zip_path)
    if not zp.exists():
        return {"ok": False, "error": f"Архив не найден: {zip_path}"}

    extract_dir = Path(f"/tmp/onboard_{zp.stem}")
    extract_dir.mkdir(exist_ok=True)

    try:
        with zipfile.ZipFile(zp, "r") as zf:
            zf.extractall(extract_dir)
    except Exception as e:
        return {"ok": False, "error": f"Ошибка распаковки: {e}"}

    # Собираем структуру
    structure = []
    readme_content = ""
    requirements = ""

    for item in sorted(extract_dir.rglob("*"))[:100]:
        rel = str(item.relative_to(extract_dir))
        if ".git" in rel:
            continue
        structure.append(rel)
        if item.name.lower() in ("readme.md", "readme.txt", "readme"):
            try:
                readme_content = item.read_text(encoding="utf-8")[:2000]
            except Exception:
                pass
        if item.name in ("requirements.txt", "pyproject.toml", "package.json"):
            try:
                requirements = item.read_text(encoding="utf-8")[:1000]
            except Exception:
                pass

    # Определяем стек
    stack = []
    exts = {Path(f).suffix for f in structure}
    if ".py" in exts:
        stack.append("Python")
    if ".js" in exts or ".ts" in exts:
        stack.append("JavaScript/TypeScript")
    if ".go" in exts:
        stack.append("Go")
    if "package.json" in structure:
        stack.append("Node.js")
    if "docker-compose.yml" in structure or "Dockerfile" in structure:
        stack.append("Docker")

    name = project_name or zp.stem
    summary = (
        f"Проект: {name} | Стек: {', '.join(stack) or 'неизвестно'}\n"
        f"Файлов: {len(structure)}\n"
        f"README: {readme_content[:300] if readme_content else 'нет'}\n"
        f"Зависимости: {requirements[:200] if requirements else 'нет'}"
    )

    # Сохраняем в KB
    if _kb_instance:
        await _kb_instance.save_entry(
            task_id=f"onboard_{name}",
            summary=summary,
            tags=["onboard", name] + stack,
            outcome="done",
        )

    return {
        "ok": True,
        "project": name,
        "stack": stack,
        "files_count": len(structure),
        "structure": structure[:30],
        "readme_preview": readme_content[:500],
        "summary": summary,
    }


# ── 9. diff_review ───────────────────────────────────────────

async def diff_review(workdir: str) -> dict:
    """Показать git diff перед коммитом."""
    r = subprocess.run(
        "git diff HEAD", shell=True, cwd=workdir,
        capture_output=True, text=True
    )
    diff = r.stdout
    stat_r = subprocess.run(
        "git diff --stat HEAD", shell=True, cwd=workdir,
        capture_output=True, text=True
    )
    return {
        "ok": True,
        "stat": stat_r.stdout.strip(),
        "diff": diff[:3000] + ("..." if len(diff) > 3000 else ""),
        "lines": len(diff.splitlines()),
    }


# ── 10. session_summary ──────────────────────────────────────

async def session_summary(send_to_tg: bool = True) -> dict:
    """Сводка дня: что было сделано + токены → отправить в TG."""
    summary_parts = ["📋 *Сводка дня*\n"]

    # KB сводка
    if _kb_instance:
        ctx = await _kb_instance.get_context()
        if ctx:
            summary_parts.append(ctx[:1500])

    # Токены
    try:
        from db.token_stats import get_stats
        stats = await get_stats(days=1)
        today = stats.get("today", {})
        tin = today.get("tin", 0) or 0
        tout = today.get("tout", 0) or 0
        summary_parts.append(
            f"\n📊 Токены сегодня: ↑{tin} + ↓{tout} = {tin+tout}"
        )
    except Exception:
        pass

    text = "\n".join(summary_parts)

    if send_to_tg and _bot_instance:
        try:
            await _bot_instance.send_message(
                chat_id=_admin_chat_id,
                text=text[:4000],
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("session_summary send error: %s", e)

    return {"ok": True, "summary": text}


# ── Регистрация ───────────────────────────────────────────────

EXTRA_REGISTRY = {
    "get_token_stats":   get_token_stats,
    "health_monitor":    health_monitor,
    "git_smart":         git_smart,
    "error_detector":    error_detector,
    "api_tester":        api_tester,
    "multi_send":        multi_send,
    "project_context":   project_context,
    "onboard_project":   onboard_project,
    "diff_review":       diff_review,
    "session_summary":   session_summary,
}

EXTRA_TOOL_SCHEMAS = [
    {
        "name": "get_token_stats",
        "description": "Статистика токенов за N дней по ключам и задачам",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "За сколько дней (default 7)"}
        }, "required": []},
    },
    {
        "name": "health_monitor",
        "description": "Проверить все сервисы (Leviathan, ArbitrCockpit, VoiceStudio и др.)",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "git_smart",
        "description": "Умный git: pull → commit → push. Использовать вместо ручных git команд.",
        "parameters": {"type": "object", "properties": {
            "workdir": {"type": "string", "description": "Путь к репозиторию"},
            "message": {"type": "string", "description": "Сообщение коммита"},
            "push":    {"type": "boolean", "description": "Пушить после коммита"},
        }, "required": ["workdir", "message"]},
    },
    {
        "name": "error_detector",
        "description": "Найти последние ошибки в логах сервиса через journalctl",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Имя сервиса (default: leviathan_agent)"},
            "lines":   {"type": "integer", "description": "Сколько строк лога смотреть"},
        }, "required": []},
    },
    {
        "name": "api_tester",
        "description": "Тест HTTP endpoint: GET/POST с payload. Показывает статус и ответ.",
        "parameters": {"type": "object", "properties": {
            "url":     {"type": "string"},
            "method":  {"type": "string", "description": "GET или POST"},
            "payload": {"type": "object", "description": "JSON тело запроса"},
        }, "required": ["url"]},
    },
    {
        "name": "multi_send",
        "description": "Отправить несколько файлов в Telegram одним вызовом",
        "parameters": {"type": "object", "properties": {
            "paths":   {"type": "array", "items": {"type": "string"}},
            "caption": {"type": "string"},
        }, "required": ["paths"]},
    },
    {
        "name": "project_context",
        "description": "Загрузить всё что агент знает о проекте из базы знаний",
        "parameters": {"type": "object", "properties": {
            "project_name": {"type": "string"},
        }, "required": ["project_name"]},
    },
    {
        "name": "onboard_project",
        "description": (
            "Аудит нового проекта из ZIP архива: распаковать, изучить структуру, "
            "определить стек, сохранить в KB. Использовать когда Denis присылает архив."
        ),
        "parameters": {"type": "object", "properties": {
            "zip_path":     {"type": "string", "description": "Путь к ZIP архиву"},
            "project_name": {"type": "string", "description": "Название проекта"},
        }, "required": ["zip_path"]},
    },
    {
        "name": "diff_review",
        "description": "Показать git diff перед коммитом — что изменилось",
        "parameters": {"type": "object", "properties": {
            "workdir": {"type": "string"},
        }, "required": ["workdir"]},
    },
    {
        "name": "session_summary",
        "description": "Сводка дня: что сделано + токены. Отправить в TG.",
        "parameters": {"type": "object", "properties": {
            "send_to_tg": {"type": "boolean", "description": "Отправить в Telegram"},
        }, "required": []},
    },
]

EXTRA_TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": s["name"],
            "description": s["description"],
            "parameters": s["parameters"],
        }
    }
    for s in EXTRA_TOOL_SCHEMAS
]


def register_extra_tools(tools_registry: dict, gemini_tools: list) -> None:
    tools_registry.update(EXTRA_REGISTRY)
    gemini_tools.extend(EXTRA_TOOL_SCHEMAS)
    logger.info("Extra tools registered: %s", list(EXTRA_REGISTRY.keys()))
