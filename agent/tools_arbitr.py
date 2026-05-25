"""
agent/tools_arbitr.py — ArbitrCockpit Tool для Leviathan Agent

Выделенный пайплайн из Arbitr Cockpit:
  LISA оценка → Blueprint рендер → Pipeline stage управление

Подключается как набор инструментов агента для работы с Kwork/фриланс заказами.
Arbitr Cockpit API: http://localhost:8090 (или ARBITR_URL из env)

Инструменты:
  arbitr_lisa_estimate    — TC-оценка по LISA формуле
  arbitr_pipeline_start   — старт стадии пайплайна для заказа
  arbitr_pipeline_status  — статус пайплайна заказа  
  arbitr_render_prompt    — рендер блюпринта для стадии
  arbitr_submit_response  — отправка ответа LLM в стадию
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _safe_json(data) -> dict | list:
    """Конвертируем в JSON-safe структуру (default=str для нестандартных типов)."""
    import json
    return json.loads(json.dumps(data, default=str, ensure_ascii=False))


ARBITR_URL = os.environ.get("ARBITR_URL", "http://localhost:8090")
ARBITR_TIMEOUT = int(os.environ.get("ARBITR_TIMEOUT", "30"))

# ─── LISA WEIGHTS (скопировано из lisa_calculator.py для автономной работы) ──

WEIGHTS_PRESETS = {
    "script":          {"l": 0.40, "i": 0.10, "s": 0.10, "a": 0.05, "u": 0.20, "c": 0.15},
    "parser":          {"l": 0.20, "i": 0.20, "s": 0.30, "a": 0.10, "u": 0.15, "c": 0.05},
    "bot_fsm":         {"l": 0.25, "i": 0.25, "s": 0.15, "a": 0.10, "u": 0.15, "c": 0.10},
    "webapp":          {"l": 0.20, "i": 0.30, "s": 0.15, "a": 0.15, "u": 0.10, "c": 0.10},
    "api_integration": {"l": 0.15, "i": 0.35, "s": 0.20, "a": 0.10, "u": 0.10, "c": 0.10},
    "ai_pipeline":     {"l": 0.30, "i": 0.20, "s": 0.10, "a": 0.10, "u": 0.20, "c": 0.10},
    "other":           {"l": 0.30, "i": 0.20, "s": 0.15, "a": 0.10, "u": 0.15, "c": 0.10},
}

TC_TABLE = [
    (4.0,  "Junior",    8,   24,   3_000,   7_000),
    (6.0,  "Mid",      24,   40,  10_000,  20_000),
    (8.0,  "Senior",   56,  112,  30_000,  80_000),
    (9.0,  "Expert",  120,  200,  80_000, 150_000),
    (10.1, "Architect",200, 400, 150_000, 500_000),
]

RISK_PREMIUMS = {
    "new_tech": 0.30, "toxic_client": 0.20, "unclear_tz": 0.40,
    "tight_deadline": 0.15, "no_prepayment": 0.10, "huge_scope": 0.25,
}


# ─── 1. LISA ESTIMATE (автономный, без сети) ─────────────────────────────────

async def arbitr_lisa_estimate(
    l: float, i: float, s: float, a: float, u: float, c: float,
    project_type: str = "other",
    risk_flags: list | None = None,
    wip_orders: int = 1,
    k_cal: float = 1.0,
) -> dict:
    """
    Рассчитывает LISA TC-оценку автономно (без запроса к Arbitr API).
    
    Параметры (1-10):
      l = Logic complexity
      i = Integration complexity
      s = State/Resilience
      a = Autonomy
      u = Uncertainty
      c = Coordination
    
    project_type: script | parser | bot_fsm | webapp | api_integration | ai_pipeline | other
    risk_flags: ["new_tech","unclear_tz","tight_deadline","huge_scope","toxic_client","no_prepayment"]
    wip_orders: количество активных заказов (WIP-фактор)
    k_cal: калибровочный коэффициент (1.0 = без корректировки)
    """
    w = WEIGHTS_PRESETS.get(project_type, WEIGHTS_PRESETS["other"])
    
    tc_raw = (
        l * w["l"] + i * w["i"] + s * w["s"] +
        a * w["a"] + u * w["u"] + c * w["c"]
    )
    k_wip = 1.0 + 0.05 * max(0, wip_orders - 1)
    risk_premium = sum(RISK_PREMIUMS.get(f, 0.0) for f in (risk_flags or []))
    tc_quote = min(tc_raw * k_cal * k_wip * (1 + risk_premium), 10.0)
    
    # Lookup TC table
    level, h_min, h_max, p_min, p_max = TC_TABLE[-1][1:]
    for tc_max, lv, hmin, hmax, pmin, pmax in TC_TABLE:
        if tc_quote <= tc_max:
            level, h_min, h_max, p_min, p_max = lv, hmin, hmax, pmin, pmax
            break
    
    return {
        "ok": True,
        "tc_raw": round(tc_raw, 2),
        "tc_quote": round(tc_quote, 2),
        "k_wip": round(k_wip, 3),
        "risk_premium": round(risk_premium, 2),
        "k_cal": k_cal,
        "level": level,
        "hours_min": h_min,
        "hours_max": h_max,
        "price_min_rub": p_min,
        "price_max_rub": p_max,
        "weights_used": w,
        "inputs": {"l":l,"i":i,"s":s,"a":a,"u":u,"c":c},
        "risk_flags": risk_flags or [],
        "project_type": project_type,
        "summary": (
            f"TC={tc_quote:.1f} → {level} | "
            f"{h_min}-{h_max}ч | "
            f"{p_min//1000}-{p_max//1000}к₽"
        ),
    }


# ─── 2. PIPELINE STATUS ───────────────────────────────────────────────────────

async def arbitr_pipeline_status(order_id: str) -> dict:
    """
    Получает статус пайплайна для заказа из Arbitr Cockpit.
    Возвращает текущую стадию, прогресс, список выполненных стадий.
    """
    try:
        async with httpx.AsyncClient(timeout=ARBITR_TIMEOUT) as client:
            r = await client.get(f"{ARBITR_URL}/api/orders/{order_id}/pipeline")
            if not r.is_success:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            data = _safe_json(r.json())
            return {"ok": True, **data}
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": f"Arbitr Cockpit недоступен на {ARBITR_URL}. Запустите: uvicorn app.main:app --port 8090"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 3. PIPELINE START STAGE ─────────────────────────────────────────────────

async def arbitr_pipeline_start(
    order_id: str,
    stage: str,
    mode: str = "auto",
    extra_context: dict | None = None,
) -> dict:
    """
    Запускает стадию пайплайна для заказа.
    
    stage: triage | risk_manager | lisa_estimator | decomposer | architect | 
           arch_auditor | developer | tester | fixer | documenter | ...
    mode: "auto" = LLM сразу вызывается | "manual" = только рендерит промт
    """
    try:
        async with httpx.AsyncClient(timeout=ARBITR_TIMEOUT) as client:
            r = await client.post(
                f"{ARBITR_URL}/api/orders/{order_id}/pipeline/advance",
                json={
                    "stage": stage,
                    "mode": mode,
                    "extra_context": extra_context or {},
                }
            )
            if not r.is_success:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            data = _safe_json(r.json())
            return {"ok": True, **data}
    except httpx.ConnectError:
        return {"ok": False, "error": f"Arbitr Cockpit недоступен на {ARBITR_URL}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 4. RENDER PROMPT ────────────────────────────────────────────────────────

async def arbitr_render_prompt(order_id: str, run_id: int) -> dict:
    """
    Получает рендеренный промт для стадии пайплайна.
    Полезно чтобы агент видел что именно нужно сделать.
    """
    try:
        async with httpx.AsyncClient(timeout=ARBITR_TIMEOUT) as client:
            r = await client.get(
                f"{ARBITR_URL}/api/orders/{order_id}/pipeline/{run_id}"
            )
            if not r.is_success:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
            data = _safe_json(r.json())
            return {
                "ok": True,
                "stage": data.get("stage"),
                "rendered_prompt": data.get("rendered_prompt", ""),
                "status": data.get("status"),
                "invocation_mode": data.get("invocation_mode"),
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 5. SUBMIT RESPONSE ──────────────────────────────────────────────────────

async def arbitr_submit_response(
    order_id: str,
    run_id: int,
    response_text: str,
) -> dict:
    """
    Отправляет ответ LLM в стадию пайплайна.
    После этого Arbitr автоматически переводит заказ в следующий статус.
    """
    try:
        async with httpx.AsyncClient(timeout=ARBITR_TIMEOUT) as client:
            r = await client.post(
                f"{ARBITR_URL}/api/orders/{order_id}/pipeline/{run_id}/submit-response",
                json={"response_text": response_text}
            )
            if not r.is_success:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            return {"ok": True, **_safe_json(r.json())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── 6. RUN AUTO (весь пайплайн автоматически) ───────────────────────────────

async def arbitr_run_auto_stage(order_id: str, run_id: int) -> dict:
    """
    Запускает автоматический LLM-вызов для уже созданной стадии.
    Arbitr сам вызывает LLM и сохраняет ответ.
    """
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{ARBITR_URL}/api/orders/{order_id}/pipeline/{run_id}/run-auto"
            )
            if not r.is_success:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            return {"ok": True, **_safe_json(r.json())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── РЕЕСТР ДЛЯ АГЕНТА ───────────────────────────────────────────────────────

ARBITR_TOOLS_REGISTRY = {
    "arbitr_lisa_estimate":    arbitr_lisa_estimate,
    "arbitr_pipeline_status":  arbitr_pipeline_status,
    "arbitr_pipeline_start":   arbitr_pipeline_start,
    "arbitr_render_prompt":    arbitr_render_prompt,
    "arbitr_submit_response":  arbitr_submit_response,
    "arbitr_run_auto_stage":   arbitr_run_auto_stage,
}

# Gemini function declarations
ARBITR_GEMINI_TOOLS = [
    {
        "name": "arbitr_lisa_estimate",
        "description": "Рассчитать LISA TC-оценку сложности проекта по 6 осям. Используй для оценки фриланс-заказов.",
        "parameters": {
            "type": "object",
            "properties": {
                "l": {"type": "number", "description": "Logic — сложность бизнес-логики (1-10)"},
                "i": {"type": "number", "description": "Integration — число и сложность интеграций (1-10)"},
                "s": {"type": "number", "description": "State/Resilience — управление состоянием (1-10)"},
                "a": {"type": "number", "description": "Autonomy — автономность модуля (1-10)"},
                "u": {"type": "number", "description": "Uncertainty — неопределённость ТЗ (1-10)"},
                "c": {"type": "number", "description": "Coordination — координация между командой (1-10)"},
                "project_type": {
                    "type": "string",
                    "description": "Тип проекта",
                    "enum": ["script","parser","bot_fsm","webapp","api_integration","ai_pipeline","other"]
                },
                "risk_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Флаги риска: new_tech, unclear_tz, tight_deadline, huge_scope, toxic_client, no_prepayment"
                },
                "wip_orders": {"type": "integer", "description": "Текущее количество активных заказов (WIP)"},
            },
            "required": ["l", "i", "s", "a", "u", "c"],
        },
    },
    {
        "name": "arbitr_pipeline_status",
        "description": "Получить статус конвейера заказа в ArbitrCockpit: текущая стадия, прогресс, история стадий.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID заказа в ArbitrCockpit"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "arbitr_pipeline_start",
        "description": "Запустить стадию конвейера для заказа (triage/architect/developer/tester и др.).",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID заказа"},
                "stage": {
                    "type": "string",
                    "description": "Стадия: triage|risk_manager|lisa_estimator|decomposer|architect|arch_auditor|developer|tester|fixer|documenter"
                },
                "mode": {"type": "string", "enum": ["auto", "manual"], "description": "auto=LLM сам отвечает, manual=ждёт человека"},
            },
            "required": ["order_id", "stage"],
        },
    },
    {
        "name": "arbitr_submit_response",
        "description": "Отправить ответ (от агента или человека) в завершённую стадию конвейера.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "run_id":   {"type": "integer", "description": "ID pipeline run"},
                "response_text": {"type": "string", "description": "Ответ LLM для стадии"},
            },
            "required": ["order_id", "run_id", "response_text"],
        },
    },
]


def register_arbitr_tools(tools_registry: dict, gemini_tools: list) -> None:
    """
    Подключает ArbitrCockpit инструменты к существующему реестру агента.
    
    Вызов из agent/tools.py:
        from agent.tools_arbitr import register_arbitr_tools
        register_arbitr_tools(TOOLS_REGISTRY, GEMINI_TOOLS)
    """
    tools_registry.update(ARBITR_TOOLS_REGISTRY)
    gemini_tools.extend(ARBITR_GEMINI_TOOLS)
    logger.info("ArbitrCockpit tools зарегистрированы: %d инструментов", len(ARBITR_TOOLS_REGISTRY))
