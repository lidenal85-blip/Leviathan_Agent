"""
OpenAI-compatible /v1 API — позволяет Cline и другим клиентам
использовать Leviathan Agent как AI-провайдер.

Base URL: http://78.17.24.96:8200/v1
API Key:  den4ik1985!
Models:   leviathan-auto | leviathan-gemini | leviathan-claude
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

oai_router = APIRouter(prefix="/v1", tags=["openai-compat"])

MCP_TOKEN = "den4ik1985!"

MODEL_MAP = {
    "leviathan-auto":   "AUTO",
    "leviathan-gemini": "GEMINI_ONLY",
    "leviathan-claude": "CLAUDE_ONLY",
    "leviathan-think":  "CLAUDE_ONLY",
}


class OAIMessage(BaseModel):
    role: str
    content: str


class OAIChatRequest(BaseModel):
    model: str = "leviathan-auto"
    messages: List[OAIMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


def _check_auth(authorization: Optional[str]) -> bool:
    if not authorization:
        return True  # без токена — пропускаем (внутренний доступ)
    token = authorization.replace("Bearer ", "").strip()
    return token == MCP_TOKEN


@oai_router.get("/models")
async def list_models(authorization: Optional[str] = Header(None)):
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 1700000000, "owned_by": "leviathan"}
            for m in MODEL_MAP
        ],
    }


@oai_router.post("/chat/completions")
async def chat_completions(
    req: OAIChatRequest,
    authorization: Optional[str] = Header(None),
):
    if not _check_auth(authorization):
        return JSONResponse(status_code=401, content={"error": {"message": "Invalid token", "type": "auth_error"}})

    # Собираем промпт
    parts = []
    for m in req.messages:
        if m.role == "system":
            parts.append(f"[SYSTEM]: {m.content}")
        elif m.role == "user":
            parts.append(m.content)
        elif m.role == "assistant":
            parts.append(f"[ASSISTANT]: {m.content}")
    prompt = "\n".join(parts)

    model_mode = MODEL_MAP.get(req.model, "AUTO")

    # Импортируем runner и storage из main (они глобальные)
    import main as _main
    _runner = _main.runner
    _storage = _main.storage

    if _runner is None:
        return JSONResponse(status_code=503, content={"error": {"message": "Agent not ready"}})

    task_id = await _runner.submit(prompt=prompt, mode="NORMAL", model_mode=model_mode)

    # Ждём результата (до 120 сек)
    for _ in range(240):
        await asyncio.sleep(0.5)
        task = _storage.get(task_id)
        if task and task.status in ("done", "failed", "error"):
            break

    task = _storage.get(task_id)
    result_text = (task.result or task.error or "No result") if task else "Timeout"
    comp_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    if req.stream:
        async def _stream():
            chunk = {
                "id": comp_id, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": req.model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": result_text}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            done_chunk = {
                "id": comp_id, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": req.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_stream(), media_type="text/event-stream")

    return {
        "id": comp_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result_text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(result_text) // 4,
            "total_tokens": (len(prompt) + len(result_text)) // 4,
        },
    }