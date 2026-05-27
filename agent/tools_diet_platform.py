"""
agent/tools_diet_platform.py — инструменты управления «Пухляшом» (Diet Platform).

Агент может управлять Diet Platform, а Diet Platform агентом не пользуется.

Инструменты:
  diet_health      — health check сервиса
  diet_search      — запустить поиск диеты
  diet_status      — статус сессии поиска
  diet_list        — список диет (одобренные)
  diet_pending     — диеты на модерации
  diet_verify      — одобрить / отклонить диету
  diet_dlq         — Dead Letter Queue (ошибки конвейера)
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("tools_diet_platform")

DIET_API_BASE = "http://127.0.0.1:8150"
TIMEOUT = 10.0


async def _get(path: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{DIET_API_BASE}{path}")
            r.raise_for_status()
            return {"ok": True, "data": r.json(), "status_code": r.status_code}
    except httpx.ConnectError:
        return {"ok": False, "error": "Сервис Diet Platform недоступен (port 8150)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _post(path: str, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{DIET_API_BASE}{path}", json=body)
            r.raise_for_status()
            return {"ok": True, "data": r.json(), "status_code": r.status_code}
    except httpx.ConnectError:
        return {"ok": False, "error": "Сервис Diet Platform недоступен (port 8150)"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════
# ИНСТРУМЕНТЫ
# ══════════════════════════════════════════════════════════

async def diet_health() -> dict:
    """Проверить здоровье сервиса Diet Platform («Пухляш»)."""
    result = await _get("/health")
    logger.info("diet_health: %s", result.get("ok"))
    return result


async def diet_search(query: str, user_id: str = "agent") -> dict:
    """
    Запустить поиск диеты в Diet Platform.
    Возвращает session_id для отслеживания статуса через diet_status.
    """
    result = await _post("/api/v1/search", {"query": query, "user_id": user_id})
    logger.info("diet_search: query=%r ok=%s", query[:50], result.get("ok"))
    return result


async def diet_status(session_id: str) -> dict:
    """Получить статус сессии поиска Diet Platform."""
    result = await _get(f"/api/v1/sessions/{session_id}")
    logger.info("diet_status: session=%s ok=%s", session_id[:16], result.get("ok"))
    return result


async def diet_list(query: str = "", limit: int = 10, status: str = "approved") -> dict:
    """Получить список диет из Diet Platform."""
    params = f"?status={status}&limit={limit}"
    if query:
        params += f"&q={query}"
    result = await _get(f"/api/v1/diets{params}")
    logger.info("diet_list: status=%s count=%s", status, result.get("data", {}).get("count", "?"))
    return result


async def diet_pending(limit: int = 10) -> dict:
    """Получить диеты, ожидающие модерации врача."""
    result = await _get(f"/api/v1/pending?limit={limit}")
    logger.info("diet_pending: count=%s", result.get("data", {}).get("count", "?"))
    return result


async def diet_verify(diet_id: str, approved: bool, actor: str = "leviathan_agent") -> dict:
    """
    Одобрить или отклонить диету в реестре.
    approved=True — одобрить, approved=False — отклонить.
    """
    result = await _post(
        f"/api/v1/diets/{diet_id}/verify",
        {"approved": approved, "actor": actor},
    )
    logger.info("diet_verify: diet_id=%s approved=%s ok=%s", diet_id, approved, result.get("ok"))
    return result


async def diet_dlq(limit: int = 10) -> dict:
    """Получить Dead Letter Queue Diet Platform (необработанные задачи)."""
    result = await _get(f"/api/v1/dlq?limit={limit}")
    logger.info("diet_dlq: count=%s", result.get("data", {}).get("count", "?"))
    return result


# ══════════════════════════════════════════════════════════
# РЕГИСТРАЦИЯ В TOOLS_REGISTRY
# ══════════════════════════════════════════════════════════

DIET_TOOL_SCHEMAS = [
    {
        "name": "diet_health",
        "description": (
            "Проверить здоровье сервиса Diet Platform («Пухляш») на порту 8150. "
            "Используй прежде любых других diet_* инструментов."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "diet_search",
        "description": (
            "Запустить поиск диеты в Diet Platform. "
            "Возвращает session_id — используй diet_status для проверки результата. "
            "Пример: 'кето диета при диабете 2 типа'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query":   {"type": "string", "description": "Текстовый поисковый запрос диеты"},
                "user_id": {"type": "string", "description": "ID пользователя (по умолчанию 'agent')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "diet_status",
        "description": "Получить статус сессии поиска Diet Platform по session_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "UUID сессии из diet_search"}
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "diet_list",
        "description": (
            "Получить список диет из Diet Platform. "
            "Статус: approved (верифицированы), pending_verification (ждут)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query":  {"type": "string",  "description": "Поиск по названию (optional)"},
                "limit":  {"type": "integer", "description": "Кол-во результатов (1-50)"},
                "status": {"type": "string",  "description": "approved | pending_verification | all"},
            },
            "required": [],
        },
    },
    {
        "name": "diet_pending",
        "description": "Получить диеты, ожидающие модерации врача / модератора в Diet Platform.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Кол-во записей (max 50)"}
            },
            "required": [],
        },
    },
    {
        "name": "diet_verify",
        "description": (
            "Одобрить или отклонить диету в Diet Platform по её diet_id. "
            "approved=true — одобрить, approved=false — отклонить."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "diet_id":  {"type": "string",  "description": "UUID диеты"},
                "approved": {"type": "boolean", "description": "true — одобрить, false — отклонить"},
                "actor":    {"type": "string",  "description": "Кто принял решение"},
            },
            "required": ["diet_id", "approved"],
        },
    },
    {
        "name": "diet_dlq",
        "description": (
            "Получить Dead Letter Queue Diet Platform — задачи, которые не удалось обработать. "
            "Используй для диагностики ошибок конвейера."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Кол-во записей"}
            },
            "required": [],
        },
    },
]

DIET_TOOL_HANDLERS = {
    "diet_health":  diet_health,
    "diet_search":  diet_search,
    "diet_status":  diet_status,
    "diet_list":    diet_list,
    "diet_pending": diet_pending,
    "diet_verify":  diet_verify,
    "diet_dlq":     diet_dlq,
}


def register_diet_tools(registry: dict, gemini_tools: list) -> None:
    """Register Diet Platform tools into agent's TOOLS_REGISTRY and GEMINI_TOOLS."""
    registry.update(DIET_TOOL_HANDLERS)
    gemini_tools.extend(DIET_TOOL_SCHEMAS)
    logger.info("🥬 Diet Platform tools registered (%d tools)", len(DIET_TOOL_HANDLERS))