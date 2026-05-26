"""
test_claude_adapter.py — тесты ClaudeAdapter (unit, без сетевых запросов)

Тесты:
  1. RateLimitInfo.from_headers — парсинг заголовков
  2. _parse_sse_line — text_delta / message_delta / [DONE] / мусор
  3. _parse_stop_reason — stop_reason из SSE
  4. _raise_for_status — 401 → ClaudeAuthError, 429 → ClaudeRateLimitError, 500 → ClaudeServerError
  5. _build_completion_body — структура тела запроса
  6. RateLimitInfo.is_exhausted — remaining == 0
  7. ClaudeRateLimitError.next_reset_ts — проброс времени сброса
"""
import asyncio
import sys
import time
from unittest.mock import MagicMock

from claude_manager.providers.claude.adapter import (
    ClaudeAdapter,
    ClaudeAuthError,
    ClaudeRateLimitError,
    ClaudeServerError,
    RateLimitInfo,
    ClaudeMessage,
)

PASSED = []
FAILED = []

def ok(name: str):
    PASSED.append(name)
    print(f"  ✅ {name}")

def fail(name: str, reason: str):
    FAILED.append(name)
    print(f"  ❌ {name}: {reason}")

# ── 1. RateLimitInfo.from_headers ─────────────────────────────────────
print("\n[1] RateLimitInfo.from_headers")
try:
    import httpx
    headers = httpx.Headers({
        "x-ratelimit-remaining-requests": "42",
        "x-ratelimit-remaining-tokens": "5000",
        "x-ratelimit-reset": "0",  # unix timestamp 0 — фолбек
    })
    rl = RateLimitInfo.from_headers(headers)
    assert rl.remaining_requests == 42, f"remaining={rl.remaining_requests}"
    assert rl.remaining_tokens == 5000, f"tokens={rl.remaining_tokens}"
    assert rl.is_exhausted == False
    ok("from_headers: парсинг remaining + tokens")
except Exception as e:
    fail("from_headers: парсинг", str(e))

try:
    headers_zero = httpx.Headers({"x-ratelimit-remaining-requests": "0"})
    rl_zero = RateLimitInfo.from_headers(headers_zero)
    assert rl_zero.is_exhausted == True
    ok("from_headers: is_exhausted=True при remaining=0")
except Exception as e:
    fail("from_headers: is_exhausted", str(e))

try:
    # ISO-8601 timestamp
    headers_iso = httpx.Headers({"x-ratelimit-reset": "2026-05-26T12:00:00+00:00"})
    rl_iso = RateLimitInfo.from_headers(headers_iso)
    assert rl_iso.next_reset_ts > 0
    ok("from_headers: ISO-8601 reset_ts")
except Exception as e:
    fail("from_headers: ISO-8601", str(e))

# ── 2. _parse_sse_line ────────────────────────────────────────────────
print("\n[2] _parse_sse_line")
try:
    line = 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}'
    result = ClaudeAdapter._parse_sse_line(line)
    assert result == "Hello", f"result={result}"
    ok("_parse_sse_line: text_delta")
except Exception as e:
    fail("_parse_sse_line: text_delta", str(e))

try:
    result_done = ClaudeAdapter._parse_sse_line("data: [DONE]")
    assert result_done is None
    ok("_parse_sse_line: [DONE] → None")
except Exception as e:
    fail("_parse_sse_line: [DONE]", str(e))

try:
    result_garbage = ClaudeAdapter._parse_sse_line("event: message_start")
    assert result_garbage is None
    ok("_parse_sse_line: не-data строка → None")
except Exception as e:
    fail("_parse_sse_line: garbage", str(e))

try:
    result_bad_json = ClaudeAdapter._parse_sse_line("data: not-json")
    assert result_bad_json is None
    ok("_parse_sse_line: плохой JSON → None")
except Exception as e:
    fail("_parse_sse_line: bad json", str(e))

try:
    # другой тип события — не text_delta
    line_stop = 'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}'
    result_stop = ClaudeAdapter._parse_sse_line(line_stop)
    assert result_stop is None
    ok("_parse_sse_line: message_delta → None (не текст)")
except Exception as e:
    fail("_parse_sse_line: message_delta", str(e))

# ── 3. _parse_stop_reason ──────────────────────────────────────────────
print("\n[3] _parse_stop_reason")
try:
    line_stop = 'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}'
    reason = ClaudeAdapter._parse_stop_reason(line_stop)
    assert reason == "end_turn", f"reason={reason}"
    ok("_parse_stop_reason: end_turn")
except Exception as e:
    fail("_parse_stop_reason: end_turn", str(e))

try:
    reason_none = ClaudeAdapter._parse_stop_reason("data: [DONE]")
    assert reason_none is None
    ok("_parse_stop_reason: [DONE] → None")
except Exception as e:
    fail("_parse_stop_reason: [DONE]", str(e))

# ── 4. _raise_for_status ────────────────────────────────────────────────
print("\n[4] _raise_for_status")
adapter = ClaudeAdapter()

def _mock_resp(status_code: int, headers: dict = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {})
    return resp

try:
    adapter._raise_for_status(_mock_resp(200), context="test")
    ok("_raise_for_status: 200 — нет исключения")
except Exception as e:
    fail("_raise_for_status: 200", str(e))

try:
    raised = False
    try:
        adapter._raise_for_status(_mock_resp(401), context="test")
    except ClaudeAuthError:
        raised = True
    assert raised
    ok("_raise_for_status: 401 → ClaudeAuthError")
except Exception as e:
    fail("_raise_for_status: 401", str(e))

try:
    raised = False
    reset_ts = time.time() + 3600
    try:
        resp_429 = _mock_resp(429, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset": str(reset_ts)})
        adapter._raise_for_status(resp_429, context="test")
    except ClaudeRateLimitError as e:
        raised = True
        assert e.next_reset_ts > 0, f"next_reset_ts={e.next_reset_ts}"
    assert raised
    ok("_raise_for_status: 429 → ClaudeRateLimitError + next_reset_ts")
except Exception as e:
    fail("_raise_for_status: 429", str(e))

try:
    raised = False
    try:
        adapter._raise_for_status(_mock_resp(500), context="test")
    except ClaudeServerError as e:
        raised = True
        assert e.status_code == 500
    assert raised
    ok("_raise_for_status: 500 → ClaudeServerError")
except Exception as e:
    fail("_raise_for_status: 500", str(e))

# ── 5. _build_completion_body ────────────────────────────────────────────
print("\n[5] _build_completion_body")
try:
    body = ClaudeAdapter._build_completion_body("test prompt", "conv-uuid")
    assert body["prompt"] == "test prompt"
    assert "tools" in body
    assert "thinking" in body
    assert body["thinking"]["type"] == "disabled"
    ok("_build_completion_body: структура ок")
except Exception as e:
    fail("_build_completion_body", str(e))

# ── 6. ClaudeRateLimitError.next_reset_ts ────────────────────────────────
print("\n[6] ClaudeRateLimitError.next_reset_ts")
try:
    ts = time.time() + 999
    err = ClaudeRateLimitError("test", next_reset_ts=ts)
    assert err.next_reset_ts == ts
    ok("ClaudeRateLimitError: next_reset_ts пробрасывается в Level 6")
except Exception as e:
    fail("ClaudeRateLimitError.next_reset_ts", str(e))

# ── результат ────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
total = len(PASSED) + len(FAILED)
print(f"\u0420езультат: {len(PASSED)}/{total} пройдено")
if FAILED:
    print(f"\nПровалено:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("\n=== ALL ASSERTIONS PASSED ===")
    sys.exit(0)