"""
agent/tools_delivery.py — Инструменты доставки файлов + База знаний.

Инструменты:
  send_file_to_tg  — отправить файл документом в Telegram
  pack_to_zip      — упаковать файлы/директорию в ZIP архив
  kb_search        — поиск по базе знаний
  kb_save          — сохранить знание после задачи (ОБЯЗАТЕЛЬНО)
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger("tools_delivery")

# ── Зависимости — инжектируются из main.py ────────────────────
_bot_instance  = None
_admin_chat_id = None
_kb_instance   = None


def inject_delivery_deps(bot, chat_id: int, kb) -> None:
    """Вызвать из main.py после инициализации бота и KB."""
    global _bot_instance, _admin_chat_id, _kb_instance
    _bot_instance  = bot
    _admin_chat_id = chat_id
    _kb_instance   = kb
    logger.info("Delivery deps injected: bot=%s chat=%s", bool(bot), chat_id)


# ══════════════════════════════════════════════════════════════
# ИНСТРУМЕНТЫ
# ══════════════════════════════════════════════════════════════

async def send_file_to_tg(path: str, caption: str = "") -> dict:
    """
    Отправить файл пользователю в Telegram как документ.
    Работает с любыми файлами: .md, .pdf, .zip, .txt, .py, .json
    """
    if _bot_instance is None:
        return {"ok": False, "error": "Бот не инициализирован"}

    file_path = Path(path)
    if not file_path.exists():
        return {"ok": False, "error": f"Файл не найден: {path}"}

    size_kb = file_path.stat().st_size // 1024
    if size_kb > 50_000:
        return {"ok": False, "error": f"Файл слишком большой: {size_kb}KB (лимит 50MB)"}

    try:
        from aiogram.types import FSInputFile
        doc = FSInputFile(str(file_path), filename=file_path.name)
        cap = caption or f"📄 {file_path.name} ({size_kb}KB)"
        await _bot_instance.send_document(
            chat_id=_admin_chat_id,
            document=doc,
            caption=cap[:1024],
        )
        logger.info("send_file_to_tg: %s → TG chat %s", path, _admin_chat_id)
        return {
            "ok":       True,
            "sent":     str(file_path),
            "size_kb":  size_kb,
            "filename": file_path.name,
        }
    except Exception as e:
        logger.error("send_file_to_tg error: %s", e)
        return {"ok": False, "error": str(e)}


async def pack_to_zip(
    paths:    list[str],
    zip_name: str = "archive.zip",
    dest_dir: str = "/tmp",
) -> dict:
    """
    Упаковать список файлов или директорий в ZIP архив.
    Возвращает путь к архиву.
    """
    zip_path = Path(dest_dir) / zip_name
    try:
        packed = []
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                src = Path(p)
                if not src.exists():
                    logger.warning("pack_to_zip: не найден %s", p)
                    continue
                if src.is_dir():
                    for f in src.rglob("*"):
                        if f.is_file() and ".git" not in str(f):
                            zf.write(f, f.relative_to(src.parent))
                            packed.append(str(f))
                else:
                    zf.write(src, src.name)
                    packed.append(str(src))

        size_kb = zip_path.stat().st_size // 1024
        logger.info("pack_to_zip: %d файлов → %s (%dKB)", len(packed), zip_path, size_kb)
        return {
            "ok":      True,
            "path":    str(zip_path),
            "files":   len(packed),
            "size_kb": size_kb,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def kb_search(query: str, limit: int = 5) -> dict:
    """
    Поиск по базе знаний агента.
    Используй в начале задачи если пользователь ссылается на предыдущую работу.
    """
    if _kb_instance is None:
        return {"ok": False, "error": "База знаний не инициализирована"}
    results = await _kb_instance.search(query, limit=limit)
    files   = await _kb_instance.find_file(query)
    return {
        "ok":      True,
        "entries": results,
        "files":   files,
        "count":   len(results),
    }


async def kb_save(
    task_id:  str,
    summary:  str,
    files:    list[str] | None = None,
    tags:     list[str] | None = None,
    outcome:  str = "done",
) -> dict:
    """
    Сохранить знание/опыт в базу знаний.
    ОБЯЗАТЕЛЬНО вызывай после завершения каждой задачи.
    """
    if _kb_instance is None:
        return {"ok": False, "error": "База знаний не инициализирована"}

    entry_id = await _kb_instance.save_entry(
        task_id=task_id,
        summary=summary,
        files=files or [],
        outcome=outcome,
        tags=tags or [],
    )
    for f in (files or []):
        await _kb_instance.index_file(task_id=task_id, path=f)

    return {"ok": True, "entry_id": entry_id, "saved": summary[:100]}


# ── Схемы для Gemini function calling ─────────────────────────

DELIVERY_TOOL_SCHEMAS = [
    {
        "name": "send_file_to_tg",
        "description": (
            "Отправить файл пользователю в Telegram как документ для скачивания. "
            "ИСПОЛЬЗУЙ когда пользователь просит 'скачать', 'получить файл', "
            "'отправь архив', 'пришли отчёт', 'дай файл'. "
            "Работает с .md, .pdf, .zip, .txt, .py, .json"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Абсолютный путь к файлу"},
                "caption": {"type": "string", "description": "Подпись к файлу (опционально)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "pack_to_zip",
        "description": (
            "Упаковать файлы или директорию в ZIP архив. "
            "Используй перед send_file_to_tg если нужно отправить несколько файлов."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths":    {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список путей к файлам или директориям",
                },
                "zip_name": {"type": "string", "description": "Имя архива (напр. report.zip)"},
                "dest_dir": {"type": "string", "description": "Куда сохранить (по умолч. /tmp)"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "kb_search",
        "description": (
            "Поиск по базе знаний — найти что делал раньше, какие файлы создавал. "
            "Используй в НАЧАЛЕ задачи если пользователь ссылается на предыдущую работу."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "limit": {"type": "integer", "description": "Максимум результатов (по умолчанию 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_save",
        "description": (
            "Сохранить итог задачи в базу знаний. "
            "ОБЯЗАТЕЛЬНО вызывай в КОНЦЕ каждой задачи — это память агента."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID задачи"},
                "summary": {"type": "string", "description": "Что было сделано (2-5 предложений)"},
                "files":   {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Пути к созданным файлам",
                },
                "tags":    {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Теги: ['voicestudio', 'report', 'fix']",
                },
                "outcome": {"type": "string", "description": "done | failed | partial"},
            },
            "required": ["task_id", "summary"],
        },
    },
]

DELIVERY_REGISTRY = {
    "send_file_to_tg": send_file_to_tg,
    "pack_to_zip":     pack_to_zip,
    "kb_search":       kb_search,
    "kb_save":         kb_save,
}


def register_delivery_tools(tools_registry: dict, gemini_tools: list) -> None:
    """Подключить delivery инструменты к реестру агента."""
    tools_registry.update(DELIVERY_REGISTRY)
    gemini_tools.extend(DELIVERY_TOOL_SCHEMAS)
    logger.info("Delivery tools registered: %s", list(DELIVERY_REGISTRY.keys()))

DELIVERY_TOOLS_OPENAI = [
    {"type": "function", "function": {"name": "send_file_to_tg", "description": "Отправить файл в Telegram как документ", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "caption": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "pack_to_zip", "description": "Упаковать файлы в ZIP", "parameters": {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}, "zip_name": {"type": "string"}}, "required": ["paths"]}}},
    {"type": "function", "function": {"name": "kb_search", "description": "Поиск по базе знаний", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "kb_save", "description": "Сохранить итог задачи в базу знаний", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}, "summary": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id", "summary"]}}}
]
