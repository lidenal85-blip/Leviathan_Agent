"""
mcp_server/leviathan_mcp.py — MCP Server для Cursor IDE
Транспорт: stdio (JSON-RPC 2.0)

Cursor config (.cursor/mcp.json):
{
  "mcpServers": {
    "leviathan": {
      "command": "python3",
      "args": ["/opt/leviathan_agent/mcp_server/leviathan_mcp.py"]
    }
  }
}
"""
from __future__ import annotations
import asyncio, json, sys
import httpx

LEVIATHAN_URL = "http://localhost:8200"
ARBITR_URL    = "http://localhost:8090"

TOOLS = [
    {"name":"leviathan_task","description":"Задача на сервере через Leviathan Agent",
     "inputSchema":{"type":"object","properties":{"prompt":{"type":"string"},"mode":{"type":"string","enum":["SAFE","NORMAL","FULL"]}},"required":["prompt"]}},
    {"name":"leviathan_status","description":"Статус Leviathan Agent","inputSchema":{"type":"object","properties":{}}},
    {"name":"arbitr_lisa","description":"LISA TC-оценка сложности проекта (автономно)",
     "inputSchema":{"type":"object","properties":{"l":{"type":"number"},"i":{"type":"number"},"s":{"type":"number"},"a":{"type":"number"},"u":{"type":"number"},"c":{"type":"number"},"project_type":{"type":"string"},"risk_flags":{"type":"array","items":{"type":"string"}}},"required":["l","i","s","a","u","c"]}},
    {"name":"arbitr_pipeline_status","description":"Статус конвейера заказа","inputSchema":{"type":"object","properties":{"order_id":{"type":"string"}},"required":["order_id"]}},
    {"name":"arbitr_pipeline_advance","description":"Запустить стадию пайплайна","inputSchema":{"type":"object","properties":{"order_id":{"type":"string"},"stage":{"type":"string"},"mode":{"type":"string"}},"required":["order_id"]}},
]

WEIGHTS={"bot_fsm":{"l":0.25,"i":0.25,"s":0.15,"a":0.10,"u":0.15,"c":0.10},"webapp":{"l":0.20,"i":0.30,"s":0.15,"a":0.15,"u":0.10,"c":0.10},"other":{"l":0.30,"i":0.20,"s":0.15,"a":0.10,"u":0.15,"c":0.10}}
TC_TABLE=[(4.0,"Junior",8,24,3000,7000),(6.0,"Mid",24,40,10000,20000),(8.0,"Senior",56,112,30000,80000),(9.0,"Expert",120,200,80000,150000),(10.1,"Architect",200,400,150000,500000)]
RISKS={"new_tech":0.30,"unclear_tz":0.40,"tight_deadline":0.15,"huge_scope":0.25}

async def call_tool(name,args):
    async with httpx.AsyncClient(timeout=120) as c:
        if name=="leviathan_task":
            r=await c.post(f"{LEVIATHAN_URL}/api/tasks",json={"prompt":args["prompt"],"mode":args.get("mode","NORMAL")}); return r.json()
        elif name=="leviathan_status":
            r=await c.get(f"{LEVIATHAN_URL}/health"); return r.json()
        elif name=="arbitr_lisa":
            w=WEIGHTS.get(args.get("project_type","other"),WEIGHTS["other"])
            l,i,s,a,u,cc=args["l"],args["i"],args["s"],args["a"],args["u"],args["c"]
            tc=l*w["l"]+i*w["i"]+s*w["s"]+a*w["a"]+u*w["u"]+cc*w["c"]
            rp=sum(RISKS.get(f,0) for f in args.get("risk_flags",[]))
            tq=min(tc*(1+rp),10.0)
            lv,hm,hx,pm,px="Architect",200,400,150000,500000
            for tm,lv2,h1,h2,p1,p2 in TC_TABLE:
                if tq<=tm: lv,hm,hx,pm,px=lv2,h1,h2,p1,p2; break
            return {"ok":True,"tc":round(tq,2),"level":lv,"hours":f"{hm}-{hx}","price":f"{pm//1000}-{px//1000}к₽"}
        elif name=="arbitr_pipeline_status":
            r=await c.get(f"{ARBITR_URL}/api/orders/{args['order_id']}/pipeline"); return r.json()
        elif name=="arbitr_pipeline_advance":
            r=await c.post(f"{ARBITR_URL}/api/orders/{args['order_id']}/pipeline/advance",json={"stage":args.get("stage"),"mode":args.get("mode","manual")}); return r.json()
        return {"error":f"Unknown: {name}"}

async def handle(req):
    m,rid=req.get("method",""),req.get("id")
    if m=="initialize": return {"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"0.1.0","capabilities":{"tools":{}},"serverInfo":{"name":"leviathan-mcp","version":"1.0.0"}}}
    elif m=="tools/list": return {"jsonrpc":"2.0","id":rid,"result":{"tools":TOOLS}}
    elif m=="tools/call":
        try: res=await call_tool(req["params"]["name"],req["params"].get("arguments",{}))
        except Exception as e: res={"error":str(e)}
        return {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text","text":json.dumps(res,ensure_ascii=False,indent=2)}]}}
    return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":"Not found"}}

async def main():
    loop=asyncio.get_event_loop()
    reader=asyncio.StreamReader()
    await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin)
    while True:
        line=await reader.readline()
        if not line: break
        try:
            resp=await handle(json.loads(line.decode()))
        except Exception as e:
            resp={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":str(e)}}
        sys.stdout.buffer.write((json.dumps(resp)+"\n").encode()); sys.stdout.buffer.flush()

if __name__=="__main__": asyncio.run(main())
