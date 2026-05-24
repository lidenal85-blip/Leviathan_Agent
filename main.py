"""
main.py — LEVIATHAN AGENT сервер
FastAPI + WebSocket дашборд + Telegram polling
"""
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.core import LeviathanAgent, Task
from agent.key_pool import GeminiKeyPool
from agent.tg_bot import AgentRunner, TelegramNotifier, setup_bot_handlers, router as tg_router
from config import get_settings
from db.storage import TaskStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Глобальные объекты ──────────────────────────────────────
settings = get_settings()
storage  = TaskStorage(settings.db_path)
key_pool = GeminiKeyPool(settings.gemini_keys_list or ["placeholder"])
agent    = LeviathanAgent(key_pool, max_iterations=settings.max_iterations)
runner: AgentRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner
    await storage.init()

    # Telegram бот
    if settings.tg_bot_token and settings.tg_admin_chat_id:
        from aiogram import Bot, Dispatcher
        bot = Bot(token=settings.tg_bot_token)
        notifier = TelegramNotifier(bot, settings.tg_admin_chat_id)
        runner = AgentRunner(agent, storage, notifier)
        dp = Dispatcher()
        setup_bot_handlers(tg_router, runner, notifier)
        dp.include_router(tg_router)

        asyncio.create_task(runner.run_loop())
        asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        logger.info("Telegram бот запущен")
    else:
        logger.warning("TG не настроен — работаем без бота")
        from unittest.mock import AsyncMock, MagicMock
        notifier = MagicMock()
        notifier.on_task_start = AsyncMock()
        notifier.on_step       = AsyncMock()
        notifier.on_task_done  = AsyncMock()
        notifier.on_task_failed = AsyncMock()
        notifier.ask_approval  = AsyncMock(return_value=True)
        runner = AgentRunner(agent, storage, notifier)
        asyncio.create_task(runner.run_loop())

    yield
    logger.info("LEVIATHAN AGENT: завершение")


app = FastAPI(title="LEVIATHAN AGENT", version="1.0.0", lifespan=lifespan)


# ── REST API ─────────────────────────────────────────────────

class TaskRequest(BaseModel):
    prompt: str
    mode: str = "NORMAL"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "current_task": runner.current_task.id if runner and runner.current_task else None,
        "key_pool": key_pool.stats(),
    }


@app.post("/api/tasks", status_code=201)
async def create_task(req: TaskRequest):
    if not runner:
        raise HTTPException(503, "Runner не инициализирован")
    task = await runner.submit(req.prompt, req.mode)
    return {"task_id": task.id, "status": task.status.value}


@app.get("/api/tasks")
async def list_tasks(limit: int = 20):
    tasks = await storage.list_recent(limit)
    return [
        {
            "id": t.id, "prompt": t.prompt[:80],
            "status": t.status.value,
            "steps": len(t.steps),
            "created_at": t.created_at,
        }
        for t in tasks
    ]


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = await storage.get(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    return {
        "id": task.id,
        "prompt": task.prompt,
        "status": task.status.value,
        "result": task.result,
        "error": task.error,
        "steps": [
            {
                "idx": s.idx, "tool": s.tool,
                "args": s.args,
                "ok": s.result.get("ok", False) if s.result else False,
                "duration": s.duration,
            }
            for s in task.steps
        ],
        "created_at": task.created_at,
        "finished_at": task.finished_at,
    }


@app.delete("/api/tasks/current")
async def stop_current():
    if runner:
        runner.cancel_current()
    return {"ok": True}


# ── WebSocket live лог ────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if runner:
        runner._ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if runner:
            runner._ws_clients.discard(ws)


# ── Веб дашборд ───────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LEVIATHAN AGENT</title>
<style>
:root{--bg:#07070f;--s:#0e0e1c;--b:rgba(122,253,214,.12);--mint:#7afdd6;--red:#ff5f7e;--amber:#ffcc00;--text:#c8d8e8;--muted:#4a5568;--mono:'Courier New',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh;padding:20px}
h1{color:var(--mint);font-size:1.4rem;margin-bottom:4px}
.sub{color:var(--muted);font-size:.75rem;margin-bottom:20px}
.card{background:var(--s);border:1px solid var(--b);border-radius:8px;padding:16px;margin-bottom:16px}
textarea{width:100%;height:80px;background:#0a0a18;border:1px solid var(--b);border-radius:6px;color:var(--text);font-family:var(--mono);font-size:.85rem;padding:10px;resize:vertical}
.btn{padding:8px 20px;border:1px solid var(--mint);border-radius:6px;background:rgba(122,253,214,.1);color:var(--mint);cursor:pointer;font-family:var(--mono);font-size:.85rem;margin-top:8px}
.btn:hover{background:rgba(122,253,214,.2)}
.btn.red{border-color:var(--red);background:rgba(255,95,126,.1);color:var(--red)}
select{background:#0a0a18;border:1px solid var(--b);border-radius:6px;color:var(--text);padding:6px 10px;font-family:var(--mono);font-size:.82rem;margin-left:8px}
.log{height:300px;overflow-y:auto;font-size:.75rem;line-height:1.6}
.log-line{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.log-line.ok{color:var(--mint)}.log-line.err{color:var(--red)}.log-line.info{color:var(--text)}
.task{padding:10px;border-bottom:1px solid var(--b);display:flex;gap:10px;align-items:center}
.badge{padding:2px 8px;border-radius:4px;font-size:.7rem}
.badge.done{background:rgba(122,253,214,.15);color:var(--mint)}
.badge.failed{background:rgba(255,95,126,.15);color:var(--red)}
.badge.running{background:rgba(255,204,0,.15);color:var(--amber)}
.badge.pending{background:rgba(74,85,104,.3);color:var(--muted)}
.status-bar{display:flex;gap:16px;font-size:.75rem;color:var(--muted);margin-bottom:16px}
.status-bar span{color:var(--text)}
</style>
</head>
<body>
<h1>⚡ LEVIATHAN AGENT</h1>
<div class="sub">Autonomous Gemini DevOps Agent</div>

<div class="status-bar">
  Статус: <span id="agent-status">загрузка...</span>
  | Текущая задача: <span id="current-task">нет</span>
</div>

<div class="card">
  <div style="font-size:.85rem;color:var(--mint);margin-bottom:10px">📝 Новая задача</div>
  <textarea id="prompt" placeholder="Опиши задачу для агента...&#10;Например: Исправь 404 ошибку на /voice/upload и запушь на GitHub"></textarea>
  <br>
  Режим:
  <select id="mode">
    <option value="NORMAL">NORMAL (безопасный)</option>
    <option value="FULL">FULL (все права)</option>
    <option value="SAFE">SAFE (только чтение)</option>
  </select>
  <button class="btn" onclick="submitTask()">▶ Запустить</button>
  <button class="btn red" onclick="stopTask()" style="margin-left:8px">■ Стоп</button>
</div>

<div class="card">
  <div style="font-size:.85rem;color:var(--mint);margin-bottom:10px">📡 Live лог</div>
  <div class="log" id="log"></div>
</div>

<div class="card">
  <div style="font-size:.85rem;color:var(--mint);margin-bottom:10px">📋 История задач</div>
  <div id="tasks-list"></div>
</div>

<script>
const BASE = '';

// WebSocket
const ws = new WebSocket((location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/ws');
ws.onmessage = e => {
  const d = JSON.parse(e.data);
  addLog(d.type === 'step' ? (d.ok ? 'ok' : 'err') : 'info',
    `[${d.task_id}] ${d.tool || d.message || JSON.stringify(d)}`);
};

function addLog(type, text) {
  const log = document.getElementById('log');
  const line = document.createElement('div');
  line.className = 'log-line ' + type;
  line.textContent = new Date().toLocaleTimeString() + ' ' + text;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

async function submitTask() {
  const prompt = document.getElementById('prompt').value.trim();
  const mode = document.getElementById('mode').value;
  if (!prompt) return;
  const r = await fetch('/api/tasks', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,mode})});
  const d = await r.json();
  addLog('info', `✅ Задача #${d.task_id} запущена`);
  document.getElementById('prompt').value = '';
  loadTasks();
}

async function stopTask() {
  await fetch('/api/tasks/current', {method:'DELETE'});
  addLog('info', '🛑 Задача остановлена');
}

async function loadStatus() {
  const r = await fetch('/health');
  const d = await r.json();
  document.getElementById('agent-status').textContent = d.status;
  document.getElementById('current-task').textContent = d.current_task || 'нет';
}

async function loadTasks() {
  const r = await fetch('/api/tasks');
  const tasks = await r.json();
  const list = document.getElementById('tasks-list');
  list.innerHTML = tasks.map(t => `
    <div class="task">
      <span class="badge ${t.status}">${t.status}</span>
      <span style="flex:1;font-size:.78rem">#${t.id} ${t.prompt}</span>
      <span style="color:var(--muted);font-size:.7rem">${t.steps} шагов</span>
    </div>
  `).join('');
}

setInterval(loadStatus, 3000);
setInterval(loadTasks, 5000);
loadStatus(); loadTasks();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
