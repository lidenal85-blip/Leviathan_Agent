"""
main.py — LEVIATHAN AGENT v3.1
FastAPI + WebSocket дашборд + Telegram polling.

Инициализация (порядок важен):
1. Settings (из .env)
2. KeyPool (core_bridge — engine или bundled)
3. ClaudeAdapter (core_bridge/claude_adapter)
4. ModelRouter (agent/model_router)
5. ExecutionJournal + TaskStorage + OperationRegistry
6. LeviathanAgent (получает все зависимости)
7. AgentRunner + TelegramNotifier
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.core import LeviathanAgent, Task
from agent.model_router import ModelMode, ModelRouter, get_router
from agent.tg_bot import AgentRunner, TelegramNotifier, setup_bot_handlers, router as tg_router
from config.settings import get_settings
from core_bridge.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig, get_claude_adapter
from core_bridge.key_pool import build_key_pool
from db.journal import ExecutionJournal
from db.storage import TaskStorage
from execution.idempotency import OperationRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# ── Глобальные объекты ────────────────────────────────────────────────────────

settings      = get_settings()
key_pool      = build_key_pool(settings.gemini_keys_list)
storage       = TaskStorage(settings.db_path)
journal       = ExecutionJournal(settings.db_path)
registry      = OperationRegistry(settings.db_path)

# Claude адаптер
_claude_cfg   = ClaudeAdapterConfig(
    api_key         = getattr(settings, "anthropic_api_key", ""),
    model           = getattr(settings, "claude_model", "claude-sonnet-4-5"),
    thinking_budget = getattr(settings, "claude_thinking_budget", 10_000),
    timeout_sec     = getattr(settings, "tool_timeout_sec", 120),
)
claude_adapter = get_claude_adapter(_claude_cfg)

# Роутер моделей
model_router   = get_router(getattr(settings, "model_mode", "AUTO"))

agent = LeviathanAgent(
    key_pool       = key_pool,
    max_iterations = settings.MAX_ITERATIONS,
    journal        = journal,
    registry       = registry,
    model_name     = settings.GEMINI_MODEL,
    claude_adapter = claude_adapter,
    model_router   = model_router,
)

runner: AgentRunner | None = None


# ── NullNotifier — без mock ────────────────────────────────────────────────────

class NullNotifier:
    """Заглушка когда Telegram не настроен — без unittest.mock."""
    async def on_task_start(self, *a, **kw):  pass
    async def on_step(self, *a, **kw):         pass
    async def on_task_done(self, *a, **kw):    pass
    async def on_task_failed(self, *a, **kw):  pass
    async def ask_approval(self, *a, **kw):    return True


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global runner

    await storage.init()
    await journal.init()
    await registry.init()

    async def cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            await registry.cleanup_expired()

    asyncio.create_task(cleanup_loop())

    if settings.tg_configured:
        from aiogram import Bot, Dispatcher
        bot      = Bot(token=settings.TG_BOT_TOKEN)
        notifier = TelegramNotifier(bot, settings.TG_ADMIN_CHAT_ID)
        runner   = AgentRunner(agent, storage, notifier)
        dp       = Dispatcher()
        setup_bot_handlers(tg_router, runner, notifier)
        dp.include_router(tg_router)
        asyncio.create_task(runner.run_loop())
        asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        logger.info("Telegram бот запущен (chat_id=%s)", settings.TG_ADMIN_CHAT_ID)
    else:
        logger.warning("TG не настроен — работаем без бота")
        notifier = NullNotifier()
        runner   = AgentRunner(agent, storage, notifier)
        asyncio.create_task(runner.run_loop())

    model_mode = getattr(settings, "model_mode", "AUTO")
    logger.info(
        "LEVIATHAN AGENT v3.1 запущен — %d Gemini ключей | MODEL_MODE=%s",
        len(settings.gemini_keys_list), model_mode,
    )

    yield

    logger.info("LEVIATHAN AGENT: завершение")


app = FastAPI(title="LEVIATHAN AGENT", version="3.1.0", lifespan=lifespan)


# ══════════════════════════════════════════════════════════════════════════════
# REST API
# ══════════════════════════════════════════════════════════════════════════════

class TaskRequest(BaseModel):
    prompt:     str
    mode:       str = "NORMAL"        # SAFE | NORMAL | FULL
    model_mode: str | None = None     # GEMINI_ONLY | CLAUDE_ONLY | ... | AUTO


@app.get("/health")
async def health():
    pool_stats  = key_pool.stats() if hasattr(key_pool, "stats") else []
    model_mode  = getattr(settings, "model_mode", "AUTO")
    return {
        "status":       "ok",
        "version":      "3.1.0",
        "current_task": runner.current_task.id if runner and runner.current_task else None,
        "key_pool":     pool_stats,
        "queue_size":   runner._queue.qsize() if runner else 0,
        "model_mode":   model_mode,
    }


@app.post("/api/tasks", status_code=201)
async def create_task(req: TaskRequest):
    if not runner:
        raise HTTPException(503, "Runner не инициализирован")
    task = await runner.submit(req.prompt, req.mode, model_mode=req.model_mode)
    return {"task_id": task.id, "status": task.status.value}


@app.get("/api/tasks")
async def list_tasks(limit: int = 20):
    tasks = await storage.list_recent(limit)
    return [
        {
            "id":         t.id,
            "prompt":     t.prompt[:100],
            "status":     t.status.value,
            "steps":      len(t.steps),
            "created_at": t.created_at,
        }
        for t in tasks
    ]


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = await storage.get(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")

    journal_steps: list = []
    llm_stats:     dict = {}
    try:
        journal_steps = await journal.get_steps(task_id)
        llm_stats     = await journal.get_llm_stats(task_id)
    except Exception:
        pass

    return {
        "id":          task.id,
        "prompt":      task.prompt,
        "status":      task.status.value,
        "result":      task.result,
        "error":       task.error,
        "mode":        task.mode,
        "model_mode":  getattr(task, "model_mode", "AUTO"),
        "steps":       [
            {
                "idx":             s.idx,
                "tool":            s.tool,
                "provider":        getattr(s, "provider", "gemini"),
                "invocation_id":   s.invocation_id,
                "idempotency_key": s.idempotency_key[:8] + "..." if s.idempotency_key else "",
                "ok":              s.result.get("ok", False) if s.result else False,
                "duration":        s.duration,
            }
            for s in task.steps
        ],
        "llm_stats":   llm_stats,
        "created_at":  task.created_at,
        "finished_at": task.finished_at,
    }


@app.delete("/api/tasks/current")
async def stop_current():
    if runner:
        runner.cancel_current()
    return {"ok": True}


@app.get("/api/pool")
async def pool_status():
    return {"stats": key_pool.stats() if hasattr(key_pool, "stats") else []}


@app.get("/api/model-mode")
async def get_model_mode():
    return {"model_mode": getattr(settings, "model_mode", "AUTO")}


@app.post("/api/model-mode")
async def set_model_mode(body: dict):
    """Runtime смена режима без перезапуска."""
    new_mode = body.get("mode", "AUTO")
    try:
        mode = ModelMode(new_mode)
    except ValueError:
        raise HTTPException(400, f"Неизвестный режим: {new_mode}")
    global model_router
    model_router = get_router(mode.value)
    agent.model_router = model_router
    logger.info("MODEL_MODE изменён на %s", mode.value)
    return {"model_mode": mode.value, "ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket live лог
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# Веб-дашборд
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⚡ LEVIATHAN AGENT</title>
<style>
:root{--bg:#07070f;--s:#0e0e1c;--b:rgba(122,253,214,.12);--mint:#7afdd6;--red:#ff5f7e;--amber:#ffcc00;--blue:#7eb8ff;--purple:#c084fc;--text:#c8d8e8;--muted:#4a5568;--mono:'Courier New',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh;padding:20px}
h1{color:var(--mint);font-size:1.5rem;margin-bottom:4px;letter-spacing:2px}
.sub{color:var(--muted);font-size:.75rem;margin-bottom:20px}
.card{background:var(--s);border:1px solid var(--b);border-radius:8px;padding:16px;margin-bottom:16px}
.card-title{font-size:.85rem;color:var(--mint);margin-bottom:12px}
textarea{width:100%;height:80px;background:#0a0a18;border:1px solid var(--b);border-radius:6px;color:var(--text);font-family:var(--mono);font-size:.85rem;padding:10px;resize:vertical}
.btn{padding:8px 20px;border:1px solid var(--mint);border-radius:6px;background:rgba(122,253,214,.1);color:var(--mint);cursor:pointer;font-family:var(--mono);font-size:.85rem;margin-top:8px;margin-right:6px;transition:.2s}
.btn:hover{background:rgba(122,253,214,.2)}
.btn.red{border-color:var(--red);background:rgba(255,95,126,.1);color:var(--red)}
.btn.active{background:rgba(122,253,214,.25);font-weight:bold}
.btn-group{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.btn-model{padding:6px 14px;border:1px solid var(--muted);border-radius:5px;background:transparent;color:var(--muted);cursor:pointer;font-family:var(--mono);font-size:.75rem;transition:.2s}
.btn-model:hover{border-color:var(--purple);color:var(--purple)}
.btn-model.active{border-color:var(--purple);background:rgba(192,132,252,.15);color:var(--purple);font-weight:bold}
select{background:#0a0a18;border:1px solid var(--b);border-radius:6px;color:var(--text);padding:6px 10px;font-family:var(--mono);font-size:.82rem;margin-left:8px}
.log{height:300px;overflow-y:auto;font-size:.75rem;line-height:1.7}
.log-line{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.log-line.ok{color:var(--mint)}.log-line.err{color:var(--red)}.log-line.info{color:var(--text)}.log-line.warn{color:var(--amber)}.log-line.claude{color:var(--purple)}
.task{padding:10px 6px;border-bottom:1px solid var(--b);display:flex;gap:10px;align-items:center;cursor:pointer}
.task:hover{background:rgba(122,253,214,.04)}
.badge{padding:2px 8px;border-radius:4px;font-size:.68rem;font-weight:bold}
.badge.done{background:rgba(122,253,214,.15);color:var(--mint)}
.badge.failed{background:rgba(255,95,126,.15);color:var(--red)}
.badge.running{background:rgba(255,204,0,.15);color:var(--amber);animation:pulse 1s infinite}
.badge.pending{background:rgba(74,85,104,.3);color:var(--muted)}
.badge.cancelled{background:rgba(126,184,255,.1);color:var(--blue)}
.status-bar{display:flex;gap:20px;font-size:.75rem;color:var(--muted);margin-bottom:16px;flex-wrap:wrap}
.status-bar .val{color:var(--text)}
.status-bar .val.purple{color:var(--purple)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.pill{display:inline-block;padding:1px 6px;border-radius:3px;font-size:.65rem;background:rgba(122,253,214,.08);color:var(--mint);margin-left:4px}
.pill.claude{background:rgba(192,132,252,.1);color:var(--purple)}
.label{font-size:.75rem;color:var(--muted);margin-right:4px}
</style>
</head>
<body>
<h1>⚡ LEVIATHAN AGENT</h1>
<div class="sub">v3.1 · Gemini 2.5 Flash + Claude · ExecutionJournal + Idempotency</div>

<div class="status-bar">
  Статус: <span class="val" id="agent-status">загрузка...</span>
  Задача: <span class="val" id="current-task">нет</span>
  Очередь: <span class="val" id="queue-size">0</span>
  Ключей: <span class="val" id="keys-ok">—</span>
  Режим: <span class="val purple" id="model-mode-label">AUTO</span>
</div>

<div class="grid">
  <div class="card">
    <div class="card-title">📝 Новая задача</div>
    <textarea id="prompt" placeholder="Опиши задачу для агента..."></textarea>

    <div style="margin-top:10px">
      <span class="label">Безопасность:</span>
      <select id="mode">
        <option value="NORMAL">NORMAL — безопасный</option>
        <option value="SAFE">SAFE — только чтение</option>
        <option value="FULL">FULL — все права + git push</option>
      </select>
    </div>

    <div style="margin-top:10px">
      <span class="label">Модель:</span>
      <div class="btn-group" id="model-btns">
        <button class="btn-model" data-mode="GEMINI_ONLY"   onclick="setModelMode(this)">⚡ Gemini only</button>
        <button class="btn-model" data-mode="CLAUDE_ONLY"   onclick="setModelMode(this)">🧠 Claude only</button>
        <button class="btn-model" data-mode="GEMINI_THINK_CLAUDE" onclick="setModelMode(this)">⚡→🧠 G+C</button>
        <button class="btn-model" data-mode="CLAUDE_THINK_GEMINI" onclick="setModelMode(this)">🧠→⚡ C+G</button>
        <button class="btn-model active" data-mode="AUTO"   onclick="setModelMode(this)">🔀 AUTO</button>
      </div>
    </div>

    <div style="margin-top:10px">
      <button class="btn" onclick="submitTask()">▶ Запустить</button>
      <button class="btn red" onclick="stopTask()">■ Стоп</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">📡 Live лог</div>
    <div class="log" id="log"></div>
  </div>
</div>

<div class="card">
  <div class="card-title">📋 История задач</div>
  <div id="tasks-list"></div>
</div>

<script>
const BASE='';
let ws;
let currentModelMode = 'AUTO';

function initWS(){
  ws = new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/ws');
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    const type = d.provider==='claude' ? 'claude' : (d.ok ? 'ok' : 'err');
    const provPill = d.provider ? ` <span class="pill ${d.provider==='claude'?'claude':''}">${d.provider||'gemini'}</span>` : '';
    const cached   = d.cached ? ' <span class="pill">cached</span>' : '';
    const dur      = d.duration ? ' ⏱'+d.duration.toFixed(1)+'s' : '';
    addLog(type, `[${d.task_id||''}] ${d.tool||d.message||JSON.stringify(d)}${dur}${provPill}${cached}`);
  };
  ws.onclose = () => setTimeout(initWS, 3000);
}
initWS();

function addLog(type, text){
  const log  = document.getElementById('log');
  const line = document.createElement('div');
  line.className = 'log-line ' + type;
  line.innerHTML = new Date().toLocaleTimeString() + ' ' + text;
  log.appendChild(line);
  if(log.children.length > 300) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

function setModelMode(btn){
  currentModelMode = btn.dataset.mode;
  document.querySelectorAll('.btn-model').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // Сохраняем на сервере
  fetch('/api/model-mode', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({mode: currentModelMode})
  }).then(r=>r.json()).then(d=>{
    document.getElementById('model-mode-label').textContent = d.model_mode;
    addLog('info', '🔀 MODEL_MODE → ' + d.model_mode);
  });
}

async function submitTask(){
  const prompt = document.getElementById('prompt').value.trim();
  const mode   = document.getElementById('mode').value;
  if(!prompt) return;
  const r = await fetch('/api/tasks', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({prompt, mode, model_mode: currentModelMode})
  });
  const d = await r.json();
  addLog('info', '✅ Задача #'+d.task_id+' принята ('+mode+' / '+currentModelMode+')');
  document.getElementById('prompt').value = '';
  loadTasks();
}

async function stopTask(){
  await fetch('/api/tasks/current', {method:'DELETE'});
  addLog('warn', '🛑 Задача остановлена');
}

async function loadStatus(){
  const r = await fetch('/health');
  const d = await r.json();
  document.getElementById('agent-status').textContent  = d.status;
  document.getElementById('current-task').textContent  = d.current_task || 'нет';
  document.getElementById('queue-size').textContent    = d.queue_size || '0';
  document.getElementById('model-mode-label').textContent = d.model_mode || 'AUTO';
  const avail = (d.key_pool||[]).filter(k=>k.available!==false).length;
  document.getElementById('keys-ok').textContent = avail+'/'+(d.key_pool||[]).length;
  // Синхронизируем кнопки
  document.querySelectorAll('.btn-model').forEach(b=>{
    b.classList.toggle('active', b.dataset.mode === d.model_mode);
  });
}

async function loadTasks(){
  const r = await fetch('/api/tasks');
  const tasks = await r.json();
  document.getElementById('tasks-list').innerHTML = tasks.map(t=>`
    <div class="task" onclick="showTask('${t.id}')">
      <span class="badge ${t.status}">${t.status}</span>
      <span style="flex:1;font-size:.78rem">#${t.id} ${t.prompt}</span>
      <span style="color:var(--muted);font-size:.7rem">${t.steps} шагов</span>
    </div>
  `).join('');
}

async function showTask(id){
  const r = await fetch('/api/tasks/'+id);
  const t = await r.json();
  const steps = t.steps.map(s=>
    ` ${s.ok?'✅':'❌'} [${s.provider||'gemini'}] ${s.tool} [${s.duration.toFixed(1)}s]${s.idempotency_key?' 🔑'+s.idempotency_key:''}`
  ).join('\\n');
  const llm = t.llm_stats;
  alert(
    `Задача #${t.id} [${t.status}]\\n` +
    `Режим: ${t.mode} | Модель: ${t.model_mode||'AUTO'}\\n` +
    `Шагов: ${t.steps.length} | LLM: ${llm.calls||0} вызовов | Tokens: ${(llm.total_input||0)+(llm.total_output||0)}\\n\\n` +
    `Шаги:\\n${steps}\\n\\nРезультат:\\n${t.result||t.error||'—'}`
  );
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
