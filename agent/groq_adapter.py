"""
agent/groq_adapter.py — Groq function-calling loop для LEVIATHAN AGENT
Аналог _run_gemini_loop, но через Groq OpenAI-совместимый API.
Модель: llama-3.3-70b-versatile (поддерживает FC).
"""
from __future__ import annotations
import json, logging, time
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from agent.core import LeviathanAgent, Task

logger = logging.getLogger("agent.groq")
GROQ_MODEL = "llama-3.3-70b-versatile"


def _to_openai_tools(gemini_tools: list) -> list:
    """Gemini-формат → OpenAI-формат. Убираем поля которые Groq не понимает."""
    DROP = {"default", "format", "example"}
    result = []
    for t in gemini_tools:
        props = {}
        for k, v in t.get("parameters", {}).get("properties", {}).items():
            props[k] = {kk: vv for kk, vv in v.items() if kk not in DROP}
        params = dict(t.get("parameters", {}))
        params["properties"] = props
        result.append({"type": "function", "function": {
            "name":        t["name"],
            "description": t.get("description", ""),
            "parameters":  params,
        }})
    return result


async def run_groq_loop(agent: "LeviathanAgent", task: "Task") -> "Task":
    """FC-loop через Groq. Если нет ключей — fallback на Gemini."""
    from agent.core import TaskStatus, SYSTEM_PROMPT
    from agent.tools import TOOLS_REGISTRY, GEMINI_TOOLS
    from config.settings import get_settings
    import groq as groq_sdk

    s = get_settings()
    keys = [getattr(s, f"GROQ_K{i}", "").strip() for i in range(1, 6)
            if getattr(s, f"GROQ_K{i}", "").strip()]
    if not keys:
        logger.warning("Groq loop: нет ключей → Gemini")
        return await agent._run_gemini_loop(task)

    tools     = _to_openai_tools(GEMINI_TOOLS)
    messages  = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": task.prompt},
    ]
    key_idx = 0

    for iteration in range(agent.max_iterations):
        gkey   = keys[key_idx % len(keys)]
        client = groq_sdk.AsyncGroq(api_key=gkey)
        t0     = time.time()
        try:
            resp = await client.chat.completions.create(
                model=GROQ_MODEL, messages=messages,
                tools=tools, tool_choice="auto", max_tokens=4096,
            )
        except Exception as e:
            logger.warning("Groq [%d] ...%s: %s", iteration, gkey[-6:], e)
            key_idx += 1
            if key_idx >= len(keys):
                task.status = TaskStatus.FAILED
                task.error  = f"Groq: все ключи исчерпаны ({e})"
                return task
            continue

        choice  = resp.choices[0]
        msg     = choice.message
        content = msg.content or ""
        tcs     = msg.tool_calls or []
        logger.info("Groq [%d] finish=%s tools=%d %dms",
                    iteration, choice.finish_reason, len(tcs),
                    int((time.time() - t0) * 1000))
        if content:
            logger.info("Groq [%d]: %s", iteration, content[:120])

        asst: dict = {"role": "assistant", "content": content}
        if tcs:
            asst["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs
            ]
        messages.append(asst)

        if not tcs or choice.finish_reason == "stop":
            task.status      = TaskStatus.DONE
            task.result      = content or f"[Groq {GROQ_MODEL}] задача выполнена"
            task.finished_at = time.time()
            return task

        for tc in tcs:
            fn   = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            tool_fn = TOOLS_REGISTRY.get(fn)
            try:
                raw = await tool_fn(**args) if tool_fn else {"ok": False, "error": f"unknown: {fn}"}
                out = json.dumps(raw, ensure_ascii=False)
            except Exception as ex:
                out = json.dumps({"ok": False, "error": str(ex)})
            logger.info("Groq tool %s → %s", fn, out[:80])
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})

    task.status = TaskStatus.FAILED
    task.error  = f"Groq: превышен лимит ({agent.max_iterations})"
    return task