# Интеграция Leviathan Agent ↔ Cursor IDE

## Концепция
Cursor видит Leviathan Agent как:
1. **MCP сервер** — Cursor вызывает инструменты агента напрямую
2. **REST API клиент** — Cursor Rules вызывают агента через HTTP  
3. **Shared filesystem** — Cursor и агент работают с одними файлами

---

## Вариант 1: MCP Server (рекомендуемый)

### Добавить MCP адаптер в агент

Файл `mcp_server/leviathan_mcp.py`:

```python
"""
mcp_server/leviathan_mcp.py
MCP (Model Context Protocol) сервер поверх Leviathan Agent.
Cursor подключает его через .cursor/mcp.json
"""
import asyncio
import json
import sys
from typing import Any

# MCP Protocol — stdio transport
class LeviathanMCPServer:
    """
    Экспортирует инструменты агента как MCP tools.
    Cursor вызывает их через JSON-RPC 2.0 поверх stdio.
    """
    
    TOOLS = [
        {
            "name": "leviathan_task",
            "description": "Выполнить задачу через Leviathan Agent (bash, файлы, git, http)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Задача для агента"},
                    "mode": {
                        "type": "string",
                        "enum": ["SAFE", "NORMAL", "FULL"],
                        "description": "Режим: SAFE=только чтение, NORMAL=безопасный, FULL=все права"
                    }
                },
                "required": ["prompt"]
            }
        },
        {
            "name": "leviathan_bash",
            "description": "Выполнить bash команду на сервере через Leviathan",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Bash команда"},
                    "workdir": {"type": "string", "description": "Рабочая директория"}
                },
                "required": ["cmd"]
            }
        },
        {
            "name": "leviathan_status",
            "description": "Получить статус Leviathan Agent (ключи, текущая задача, очередь)",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "arbitr_lisa",
            "description": "Рассчитать LISA TC-оценку сложности проекта",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "l": {"type": "number", "description": "Logic complexity 1-10"},
                    "i": {"type": "number", "description": "Integration complexity 1-10"},
                    "s": {"type": "number", "description": "Resilience/State 1-10"},
                    "a": {"type": "number", "description": "Autonomy 1-10"},
                    "u": {"type": "number", "description": "Uncertainty 1-10"},
                    "c": {"type": "number", "description": "Coordination 1-10"},
                    "project_type": {"type": "string", "enum": ["bot_fsm","webapp","parser","api_integration","script","other"]},
                    "risk_flags": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["l","i","s","a","u","c"]
            }
        },
        {
            "name": "arbitr_pipeline_run",
            "description": "Запустить стадию Arbitr Cockpit pipeline для заказа",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "stage": {"type": "string", "description": "Название стадии (triage, architect, developer...)"},
                    "mode": {"type": "string", "enum": ["manual", "auto"]}
                },
                "required": ["order_id", "stage"]
            }
        }
    ]
    
    async def handle_request(self, req: dict) -> dict:
        method = req.get("method")
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": req["id"],
                "result": {
                    "protocolVersion": "0.1.0",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "leviathan-mcp", "version": "1.0.0"}
                }
            }
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0", "id": req["id"],
                "result": {"tools": self.TOOLS}
            }
        
        elif method == "tools/call":
            tool_name = req["params"]["name"]
            args = req["params"].get("arguments", {})
            result = await self._call_tool(tool_name, args)
            return {
                "jsonrpc": "2.0", "id": req["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            }
        
        return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": "Not found"}}
    
    async def _call_tool(self, name: str, args: dict) -> dict:
        import httpx
        BASE = "http://localhost:8200"
        
        async with httpx.AsyncClient(timeout=120) as client:
            if name == "leviathan_task":
                r = await client.post(f"{BASE}/api/tasks", json={
                    "prompt": args["prompt"],
                    "mode": args.get("mode", "NORMAL")
                })
                return r.json()
            
            elif name == "leviathan_bash":
                # Быстрый способ — через задачу агента
                r = await client.post(f"{BASE}/api/tasks", json={
                    "prompt": f"Выполни команду и сообщи результат: {args['cmd']}",
                    "mode": "NORMAL"
                })
                return r.json()
            
            elif name == "leviathan_status":
                r = await client.get(f"{BASE}/health")
                return r.json()
            
            elif name == "arbitr_lisa":
                r = await client.post("http://localhost:8090/api/lisa/compute", json=args)
                return r.json()
            
            elif name == "arbitr_pipeline_run":
                r = await client.post(
                    f"http://localhost:8090/api/orders/{args['order_id']}/pipeline/advance",
                    json={"stage": args["stage"], "mode": args.get("mode", "manual")}
                )
                return r.json()
            
            return {"error": f"Unknown tool: {name}"}
    
    async def run(self):
        """Stdio транспорт для MCP."""
        reader = asyncio.StreamReader()
        proto = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: proto, sys.stdin)
        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.BaseProtocol, sys.stdout
        )
        
        while True:
            line = await reader.readline()
            if not line:
                break
            req = json.loads(line.decode())
            resp = await self.handle_request(req)
            output = json.dumps(resp) + "\n"
            sys.stdout.buffer.write(output.encode())
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    asyncio.run(LeviathanMCPServer().run())
```

### Конфиг Cursor (.cursor/mcp.json)

```json
{
  "mcpServers": {
    "leviathan": {
      "command": "python3",
      "args": ["/opt/leviathan_agent/mcp_server/leviathan_mcp.py"],
      "env": {}
    }
  }
}
```

> Файл кладётся в корень проекта Cursor или `~/.cursor/mcp.json` (глобально).

---

## Вариант 2: Cursor Rules + REST API

Файл `.cursorrules` в проекте:

```markdown
# Leviathan Agent Integration

When you need to execute commands on the server, use the Leviathan Agent API:

## Agent Status
GET http://leviathanstory.ru:8200/health

## Submit Task  
POST http://leviathanstory.ru:8200/api/tasks
Body: {"prompt": "your task", "mode": "NORMAL"}

## Check Task
GET http://leviathanstory.ru:8200/api/tasks/{task_id}

## Projects on Server
- VoiceStudio: /var/www/voicestudio (port 8120)
- KinoVibe: /var/www/kinovibe (port 8110)
- Orionyx: /opt/orionyx (port 8005)
- AI Outreach: /opt/ai_outreach (port 8000)
- Leviathan Agent: /opt/leviathan_agent (port 8200)
- ArbitrCockpit: /opt/arbitr_cockpit (port 8090)

## Workflow
1. Read file → leviathan read_file tool
2. Edit file → write changes locally, then git commit
3. Deploy → leviathan bash_tool: systemctl restart <service>
4. Check health → curl http://leviathanstory.ru:<port>/health
```

---

## Вариант 3: Cursor Background Agent

В настройках Cursor → Background Agents → добавить:

```json
{
  "name": "Leviathan Server",
  "endpoint": "http://leviathanstory.ru:8200",
  "auth": "none",
  "capabilities": ["bash", "file_read", "file_write", "git"]
}
```

---

## Рекомендуемый порядок настройки

1. **Сейчас (быстро)**: добавить `.cursorrules` файл с описанием API
2. **Следующий шаг**: реализовать `mcp_server/leviathan_mcp.py` и `.cursor/mcp.json`
3. **Будущее**: Background Agent когда Cursor поддержит custom endpoints

