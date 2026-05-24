"""
core_bridge/claude_adapter.py — адаптер для вызова Claude
═══════════════════════════════════════════════════════════

Два пути вызова (выбирается автоматически):
  1. Claude CLI   — `claude --print ...` (если установлен на сервере)
  2. Anthropic API — httpx, без SDK (резерв или если нет CLI)

Установка CLI на сервере:
  npm install -g @anthropic-ai/claude-code
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("claude_adapter")

# ── Типы ─────────────────────────────────────────────────────────────────────

@dataclass
class ClaudeResponse:
    text:          str
    thinking:      str = ""          # thinking-блок если был
    input_tokens:  int = 0
    output_tokens: int = 0
    duration_ms:   float = 0.0
    source:        str = "api"       # "cli" | "api"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ClaudeAdapterConfig:
    api_key:          str   = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model:            str   = "claude-sonnet-4-5"
    max_tokens:       int   = 8192
    thinking_budget:  int   = 10_000   # токены на extended thinking
    timeout_sec:      float = 120.0
    prefer_cli:       bool  = True     # пробовать CLI первым


# ── CLI путь ─────────────────────────────────────────────────────────────────

async def _call_cli(prompt: str, config: ClaudeAdapterConfig) -> ClaudeResponse:
    """
    Вызов через `claude --print --output-format json`.
    Требует: npm install -g @anthropic-ai/claude-code
    """
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--model", config.model,
    ]

    t0 = time.perf_counter()
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=config.timeout_sec,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=config.timeout_sec,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"Claude CLI timeout ({config.timeout_sec}s)")
    except FileNotFoundError:
        raise RuntimeError("Claude CLI не найден. Установи: npm install -g @anthropic-ai/claude-code")

    duration_ms = (time.perf_counter() - t0) * 1000

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")
        raise RuntimeError(f"Claude CLI exit {proc.returncode}: {err[:500]}")

    raw = stdout.decode(errors="replace").strip()

    # CLI возвращает JSON или plain text в зависимости от версии
    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("content") or data.get("text") or raw
    except json.JSONDecodeError:
        text = raw

    return ClaudeResponse(
        text=text,
        duration_ms=duration_ms,
        source="cli",
    )


# ── API путь ─────────────────────────────────────────────────────────────────

async def _call_api(
    prompt:       str,
    config:       ClaudeAdapterConfig,
    use_thinking: bool = False,
    system:       str  = "",
) -> ClaudeResponse:
    """
    Прямой вызов Anthropic API через httpx (без SDK).
    """
    if not config.api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY не задан. Добавь в .env: ANTHROPIC_API_KEY=sk-ant-..."
        )

    messages = [{"role": "user", "content": prompt}]

    body: dict = {
        "model":      config.model,
        "max_tokens": config.max_tokens,
        "messages":   messages,
    }

    if system:
        body["system"] = system

    if use_thinking:
        body["thinking"] = {
            "type":         "enabled",
            "budget_tokens": config.thinking_budget,
        }
        # При thinking нужен бюджет > max_tokens
        body["max_tokens"] = max(config.max_tokens, config.thinking_budget + 4096)

    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=config.timeout_sec) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
                # Нужен для extended thinking
                "anthropic-beta":    "interleaved-thinking-2025-05-14",
            },
            json=body,
        )

    duration_ms = (time.perf_counter() - t0) * 1000

    if resp.status_code != 200:
        raise RuntimeError(
            f"Anthropic API {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    usage = data.get("usage", {})

    # Разбираем content блоки (text + thinking)
    text_parts:     list[str] = []
    thinking_parts: list[str] = []

    for block in data.get("content", []):
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "thinking":
            thinking_parts.append(block.get("thinking", ""))

    return ClaudeResponse(
        text          = "\n".join(text_parts),
        thinking      = "\n".join(thinking_parts),
        input_tokens  = usage.get("input_tokens", 0),
        output_tokens = usage.get("output_tokens", 0),
        duration_ms   = duration_ms,
        source        = "api",
    )


# ── Публичный адаптер ─────────────────────────────────────────────────────────

class ClaudeAdapter:
    """
    Умный адаптер: пробует CLI, при ошибке — падает на API.
    """

    def __init__(self, config: ClaudeAdapterConfig | None = None):
        self.config   = config or ClaudeAdapterConfig()
        self._cli_ok: bool | None = None   # None = ещё не проверяли

    async def _check_cli(self) -> bool:
        """Проверяет наличие `claude` в PATH один раз."""
        if self._cli_ok is not None:
            return self._cli_ok
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        self._cli_ok = (proc.returncode == 0)
        if self._cli_ok:
            logger.info("Claude CLI доступен — будем использовать его")
        else:
            logger.info("Claude CLI не найден — используем Anthropic API")
        return self._cli_ok

    async def call(
        self,
        prompt:       str,
        use_thinking: bool = False,
        system:       str  = "",
    ) -> ClaudeResponse:
        """
        Основной метод вызова.

        Порядок приоритетов:
          1. CLI (если prefer_cli=True и CLI доступен И thinking не нужен)
          2. API с thinking
          3. API без thinking
        """
        # Thinking всегда через API (CLI не поддерживает extended thinking)
        if use_thinking:
            logger.debug("Claude API +thinking: %s...", prompt[:80])
            return await _call_api(prompt, self.config, use_thinking=True, system=system)

        # CLI путь
        if self.config.prefer_cli:
            cli_ok = await self._check_cli()
            if cli_ok:
                try:
                    logger.debug("Claude CLI: %s...", prompt[:80])
                    return await _call_cli(prompt, self.config)
                except RuntimeError as e:
                    logger.warning("Claude CLI упал: %s — переключаемся на API", e)
                    self._cli_ok = False

        # API fallback
        logger.debug("Claude API: %s...", prompt[:80])
        return await _call_api(prompt, self.config, system=system)

    async def call_tool(
        self,
        task_description: str,
        context:          str = "",
        use_thinking:     bool = False,
    ) -> str:
        """
        Обёртка для использования как инструмент агента.
        Возвращает просто строку ответа.
        """
        prompt = task_description
        if context:
            prompt = f"Контекст:\n{context}\n\nЗадача:\n{task_description}"

        resp = await self.call(prompt, use_thinking=use_thinking)

        if resp.thinking:
            logger.info(
                "Claude thinking (%d chars): %s...",
                len(resp.thinking), resp.thinking[:120]
            )

        logger.info(
            "Claude ответ: %d tokens, %.0fms, source=%s",
            resp.total_tokens, resp.duration_ms, resp.source
        )
        return resp.text


# ── Глобальный инстанс ────────────────────────────────────────────────────────

_adapter: ClaudeAdapter | None = None


def get_claude_adapter(config: ClaudeAdapterConfig | None = None) -> ClaudeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = ClaudeAdapter(config)
    return _adapter
