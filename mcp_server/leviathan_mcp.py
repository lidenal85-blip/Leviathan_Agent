"""
mcp_server/leviathan_mcp.py — MCP Server для Cursor IDE / Claude Desktop

ТРАНСПОРТЫ (выбирается по аргументу):
  python3 leviathan_mcp.py              → stdio (Cursor default)
  python3 leviathan_mcp.py --http 8210 → HTTP SSE (Streamable HTTP)

CURSOR CONFIG (.cursor/mcp.json):
{
  "mcpServers": {
    "leviathan": {
      "command": "python3",
      "args": ["/opt/leviathan_agent/mcp_server/leviathan_mcp.py"],
      "env": {
        "LEVIATHAN_URL": "http://localhost:8200",
        "ARBITR_URL":    "http://localhost:8090"
      }
    }
  }
}

ТЕСТ (stdio):
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \\
    python3 leviathan_mcp.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import httpx

logger = logging.getLogger("leviathan_mcp")

LEVIATHAN_URL = os.environ.get("LEVIATHAN_URL", "http://localhost:8200")
ARBITR_URL    = os.environ.get("ARBITR_URL",    "http://localhost:8090")

MCP_VERSION   = "2024-11-05"
SERVER_NAME   = "leviathan-mcp"
SERVER_VER    = "1.1.0"

# ── LISA константы (автономный расчёт без сети) ──────────────────────────────

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
    (4.0,  "Junior",     8,   24,   3_000,   7_000),
    (6.0,  "Mid",       24,   40,  10_000,  20_000),
    (8.0,  "Senior",    56,  112,  30_000,  80_000),
    (9.0,  "Expert",   120,  200,  80_000, 150_000),
    (10.1, "Architect", 200, 400, 150_000, 500_000),
]

RISK_PREMIUMS = {
    "new_tech": 0.30, "unclear_tz": 0.40, "tight_deadline": 0.15,
    "huge_scope": 0.25, "toxic_client": 0.20, "no_prepayment": 0.10,
}


# ── Инструменты ──────────────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "leviathan_task",
        "description": (
            "Отправить задачу Leviathan Agent на сервере leviathanstory.ru. "
            "Агент автономно выполнит её: читает файлы, запускает команды, "
            "делает git push. Возвращает task_id для отслеживания."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string",  "description": "Задача на русском языке"},
                "mode":   {
                    "type": "string",
                    "enum": ["SAFE", "NORMAL", "FULL"],
                    "description": "SAFE=только чтение, NORMAL=безопасный, FULL=все права+git",
                    "default": "NORMAL",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "leviathan_status",
        "description": "Статус Leviathan Agent: текущая задача, очередь, состояние ключей Gemini.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "leviathan_task_result",
        "description": "Получить результат задачи по task_id (шаги, статус, вывод).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID задачи из leviathan_task"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "leviathan_logs",
        "description": "Последние задачи агента с краткой информацией.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Количество задач (по умолчанию 10)", "default": 10},
            },
        },
    },
    {
        "name": "arbitr_lisa",
        "description": (
            "Расчёт LISA TC-оценки сложности проекта по 6 осям (автономно, без сети). "
            "Используй для оценки фриланс-заказов с Kwork/FL.ru."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "l": {"type": "number", "description": "Logic (1-10) — сложность бизнес-логики"},
                "i": {"type": "number", "description": "Integration (1-10) — кол-во/сложность интеграций"},
                "s": {"type": "number", "description": "State/Resilience (1-10) — управление состоянием"},
                "a": {"type": "number", "description": "Autonomy (1-10) — автономность модуля"},
                "u": {"type": "number", "description": "Uncertainty (1-10) — неопределённость ТЗ"},
                "c": {"type": "number", "description": "Coordination (1-10) — командная координация"},
                "project_type": {
                    "type": "string",
                    "enum": ["script","parser","bot_fsm","webapp","api_integration","ai_pipeline","other"],
                    "description": "Тип проекта",
                    "default": "other",
                },
                "risk_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Флаги риска: new_tech, unclear_tz, tight_deadline, huge_scope, toxic_client, no_prepayment",
                },
            },
            "required": ["l", "i", "s", "a", "u", "c"],
        },
    },
    {
        "name": "arbitr_pipeline_status",
        "description": "Статус конвейера заказа в ArbitrCockpit (текущая стадия, прогресс).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID заказа в ArbitrCockpit"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "arbitr_pipeline_advance",
        "description": "Запустить следующую стадию конвейера для заказа (triage/architect/developer/tester...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID заказа"},
                "stage":    {"type": "string", "description": "Имя стадии (если пропустить — следующая по очереди)"},
                "mode":     {"type": "string", "enum": ["auto","manual"], "default": "auto"},
            },
            "required": ["order_id"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

async def _call_tool(name: str, args: dict) -> Any:
    timeout = httpx.Timeout(120.0)

    if name == "leviathan_task":
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{LEVIATHAN_URL}/api/tasks",
                json={"prompt": args["prompt"], "mode": args.get("mode", "NORMAL")},
            )
            data = r.json()
            return (
                f"✅ Задача принята: **#{data.get('task_id','?')}**\n"
                f"Статус: {data.get('status','?')}\n"
                f"Отслеживай через `leviathan_task_result`"
            )

    elif name == "leviathan_status":
        async with httpx.AsyncClient(timeout=timeout) as c:
            r    = await c.get(f"{LEVIATHAN_URL}/health")
            data = r.json()
        pool  = data.get("key_pool", [])
        avail = sum(1 for k in pool if k.get("available", True))
        return (
            f"⚡ LEVIATHAN AGENT\n"
            f"Статус: {data.get('status','?')}\n"
            f"Текущая задача: {data.get('current_task') or 'нет'}\n"
            f"Очередь: {data.get('queue_size', 0)}\n"
            f"Gemini ключей доступно: {avail}/{len(pool)}"
        )

    elif name == "leviathan_task_result":
        async with httpx.AsyncClient(timeout=timeout) as c:
            r    = await c.get(f"{LEVIATHAN_URL}/api/tasks/{args['task_id']}")
            data = r.json()
        steps_summary = "\n".join(
            f"  {'✅' if s.get('ok') else '❌'} [{s.get('duration',0):.1f}s] {s.get('tool','?')}"
            for s in data.get("steps", [])[:15]
        )
        return (
            f"**Задача #{data.get('id','?')}** [{data.get('status','?')}]\n"
            f"Режим: {data.get('mode','?')} | Шагов: {len(data.get('steps',[]))}\n\n"
            f"**Шаги:**\n{steps_summary or 'нет шагов'}\n\n"
            f"**Результат:**\n{data.get('result') or data.get('error') or '—'}"
        )

    elif name == "leviathan_logs":
        limit = args.get("limit", 10)
        async with httpx.AsyncClient(timeout=timeout) as c:
            r    = await c.get(f"{LEVIATHAN_URL}/api/tasks?limit={limit}")
            data = r.json()
        lines = [
            f"{'✅' if t['status']=='done' else '❌' if t['status']=='failed' else '⏳'} "
            f"#{t['id']} [{t['status']}] {t['steps']}шагов — {t['prompt'][:60]}..."
            for t in data
        ]
        return "**История задач:**\n" + "\n".join(lines) if lines else "Задач нет"

    elif name == "arbitr_lisa":
        w    = WEIGHTS_PRESETS.get(args.get("project_type", "other"), WEIGHTS_PRESETS["other"])
        l, i, s, a, u, c = args["l"], args["i"], args["s"], args["a"], args["u"], args["c"]
        tc   = l*w["l"] + i*w["i"] + s*w["s"] + a*w["a"] + u*w["u"] + c*w["c"]
        rp   = sum(RISK_PREMIUMS.get(f, 0) for f in args.get("risk_flags", []))
        tq   = min(tc * (1 + rp), 10.0)
        lv, hm, hx, pm, px = "Architect", 200, 400, 150_000, 500_000
        for tm, lv2, h1, h2, p1, p2 in TC_TABLE:
            if tq <= tm:
                lv, hm, hx, pm, px = lv2, h1, h2, p1, p2
                break
        return (
            f"**LISA TC-оценка**\n"
            f"TC = {tq:.1f} → **{lv}**\n"
            f"Часы: {hm}–{hx}ч\n"
            f"Стоимость: {pm//1000}–{px//1000}к₽\n"
            f"Риск-надбавка: +{int(rp*100)}%\n"
            f"Флаги: {', '.join(args.get('risk_flags',[])) or 'нет'}"
        )

    elif name == "arbitr_pipeline_status":
        async with httpx.AsyncClient(timeout=timeout) as c:
            r    = await c.get(f"{ARBITR_URL}/api/orders/{args['order_id']}/pipeline")
            data = r.json()
        return json.dumps(data, ensure_ascii=False, indent=2)

    elif name == "arbitr_pipeline_advance":
        body = {"mode": args.get("mode", "auto")}
        if "stage" in args:
            body["stage"] = args["stage"]
        async with httpx.AsyncClient(timeout=timeout) as c:
            r    = await c.post(
                f"{ARBITR_URL}/api/orders/{args['order_id']}/pipeline/advance",
                json=body,
            )
            data = r.json()
        return json.dumps(data, ensure_ascii=False, indent=2)

    return {"error": f"Неизвестный инструмент: {name}"}


# ── JSON-RPC handler ──────────────────────────────────────────────────────────

async def handle_rpc(req: dict) -> dict:
    method = req.get("method", "")
    rid    = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": MCP_VERSION,
                "capabilities":    {"tools": {"listChanged": False}},
                "serverInfo":      {"name": SERVER_NAME, "version": SERVER_VER},
            },
        }

    elif method in ("notifications/initialized", "ping"):
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    elif method == "tools/call":
        tool_name = req["params"]["name"]
        tool_args = req["params"].get("arguments", {})
        try:
            result = await _call_tool(tool_name, tool_args)
            if isinstance(result, str):
                content = [{"type": "text", "text": result}]
            else:
                content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
        except httpx.ConnectError as e:
            content = [{"type": "text", "text": f"❌ Сервис недоступен: {e}"}]
        except Exception as e:
            content = [{"type": "text", "text": f"❌ Ошибка: {e}"}]
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {"content": content, "isError": False},
        }

    return {
        "jsonrpc": "2.0", "id": rid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ── Транспорт: stdio ──────────────────────────────────────────────────────────

async def run_stdio() -> None:
    loop   = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req  = json.loads(line.decode())
            resp = await handle_rpc(req)
        except Exception as e:
            resp = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": str(e)},
            }
        sys.stdout.buffer.write((json.dumps(resp) + "\n").encode())
        sys.stdout.buffer.flush()


# ── Транспорт: HTTP SSE (Streamable HTTP) ─────────────────────────────────────

async def run_http(port: int) -> None:
    """
    Простой HTTP MCP transport для удалённых клиентов.
    POST /mcp → JSON-RPC запрос → JSON-RPC ответ
    GET  /    → server info
    """
    from aiohttp import web

    async def mcp_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            resp = await handle_rpc(body)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": str(e)}}
        return web.json_response(resp, headers={"Access-Control-Allow-Origin": "*"})

    async def info_handler(request: web.Request) -> web.Response:
        return web.json_response({
            "name":    SERVER_NAME,
            "version": SERVER_VER,
            "tools":   len(TOOLS),
            "leviathan_url": LEVIATHAN_URL,
            "arbitr_url":    ARBITR_URL,
        })

    app = web.Application()
    app.router.add_post("/mcp", mcp_handler)
    app.router.add_get("/",     info_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site   = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"⚡ LEVIATHAN MCP HTTP: http://0.0.0.0:{port}/mcp", flush=True)
    await asyncio.Event().wait()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LEVIATHAN MCP Server")
    parser.add_argument("--http", type=int, metavar="PORT",
                        help="Запустить HTTP transport на указанном порту (напр. 8210)")
    args_parsed = parser.parse_args()

    if args_parsed.http:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            print("pip install aiohttp  # для HTTP транспорта", file=sys.stderr)
            sys.exit(1)
        asyncio.run(run_http(args_parsed.http))
    else:
        asyncio.run(run_stdio())
