"""
test_pool.py — unit-тесты LLMProviderPool (без сетевых запросов)

Тесты:
  1. _pick_account: выбирает аккаунт с наибольшим remaining
  2. _pick_account: исключает заблокированные
  3. _pick_account: нет активных → None
  4. complete: успех → PoolResponse
  5. complete: 429 на всех → AllAccountsRateLimited
  6. complete: 401 → ротация → следующий аккаунт
  7. AllAccountsRateLimited.next_reset_ts — проброс для Level 6
  8. _earliest_reset_ts: находит минимум
"""
import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from claude_manager.core.storage.account_store import Account, AccountStatus
from claude_manager.providers.claude.adapter import (
    ClaudeAuthError, ClaudeRateLimitError, ClaudeResponse, RateLimitInfo,
)
from claude_manager.providers.pool import (
    AllAccountsRateLimited, LLMProviderPool, NoAccountsAvailable, PoolResponse,
)

PASSED, FAILED = [], []

def ok(name): PASSED.append(name); print(f"  \u2705 {name}")
def fail(name, reason): FAILED.append(name); print(f"  \u274c {name}: {reason}")


# ── фабрика моков ──────────────────────────────────────────────────────

def _account(acc_id: str, remaining: int = 100, status=AccountStatus.ACTIVE) -> Account:
    return Account(
        account_id=acc_id,
        email=f"{acc_id}@test.com",
        password="pw",
        session_key="sk_" + acc_id,
        status=status,
        rate_limit_remaining=remaining,
        rate_limit_reset_ts=time.time() + 3600,
    )

def _make_pool(
    accounts: list,
    adapter_mock: MagicMock = None,
    rotate_ok: bool = True,
) -> LLMProviderPool:
    store = MagicMock()
    store.list_all = AsyncMock(return_value=accounts)
    store.update_status = AsyncMock()

    lifecycle = MagicMock()
    lifecycle.get_active_accounts = AsyncMock(return_value=[
        a for a in accounts if a.status == AccountStatus.ACTIVE
    ])
    lifecycle.report_usage = AsyncMock()
    lifecycle.rotate_session_key = AsyncMock(return_value=rotate_ok)
    lifecycle.start = AsyncMock()
    lifecycle.stop = AsyncMock()

    sessions = MagicMock()
    sessions.init = AsyncMock()
    sessions.create_session = AsyncMock(return_value="new-session-id")
    sessions.get_mapping = AsyncMock(return_value=MagicMock(
        conversation_id="conv-123",
        account_id=accounts[0].account_id if accounts else "",
    ))
    sessions.update_mapping = AsyncMock()

    if adapter_mock is None:
        adapter_mock = MagicMock()
        adapter_mock.send_message = AsyncMock(return_value=ClaudeResponse(
            text="Привет!",
            conversation_id="conv-123",
            message_id="msg-1",
            rate_limit=RateLimitInfo(remaining_requests=99, next_reset_ts=time.time() + 3600),
        ))
        adapter_mock.create_conversation = AsyncMock(return_value="conv-new")
        adapter_mock.replay_messages = AsyncMock()

    pool = LLMProviderPool(
        store=store,
        lifecycle=lifecycle,
        sessions=sessions,
        adapter=adapter_mock,
    )
    return pool


# ── 1. _pick_account: выбирает наибольший remaining ──────────────────────
print("\n[1] _pick_account: выбирает наибольший remaining")
async def test_pick_best():
    accs = [_account("a", 50), _account("b", 99), _account("c", 10)]
    pool = _make_pool(accs)
    pool._lifecycle.get_active_accounts = AsyncMock(return_value=accs)
    picked = await pool._pick_account()
    assert picked.account_id == "b", f"picked={picked.account_id}"
    ok("_pick_account: выбрал acc с remaining=99")

asyncio.run(test_pick_best())


# ── 2. _pick_account: exclude ─────────────────────────────────────────────
print("\n[2] _pick_account: exclude")
async def test_pick_exclude():
    accs = [_account("a", 99), _account("b", 50)]
    pool = _make_pool(accs)
    pool._lifecycle.get_active_accounts = AsyncMock(return_value=accs)
    picked = await pool._pick_account(exclude={"a"})
    assert picked.account_id == "b", f"picked={picked.account_id}"
    ok("_pick_account: exclude работает")

asyncio.run(test_pick_exclude())


# ── 3. _pick_account: нет активных ───────────────────────────────────────
print("\n[3] _pick_account: нет активных")
async def test_pick_none():
    pool = _make_pool([])
    pool._lifecycle.get_active_accounts = AsyncMock(return_value=[])
    result = await pool._pick_account()
    assert result is None
    ok("_pick_account: пустой пул → None")

asyncio.run(test_pick_none())


# ── 4. complete: успех ────────────────────────────────────────────────────────────
print("\n[4] complete: успех")
async def test_complete_success():
    acc = _account("acc1", 80)
    pool = _make_pool([acc])
    try:
        resp = await pool.complete(prompt="Привет", session_id="ses-1")
        assert isinstance(resp, PoolResponse)
        assert resp.text == "Привет!"
        assert resp.account_id == "acc1"
        assert resp.rate_remaining == 99
        ok("complete: успех → PoolResponse")
    except Exception as e:
        fail("complete: успех", str(e))

asyncio.run(test_complete_success())


# ── 5. complete: 429 на всех → AllAccountsRateLimited ─────────────────────
print("\n[5] complete: 429 на всех → AllAccountsRateLimited")
async def test_all_rate_limited():
    reset_ts = time.time() + 600
    adapter = MagicMock()
    adapter.send_message = AsyncMock(
        side_effect=ClaudeRateLimitError("429", next_reset_ts=reset_ts)
    )
    adapter.create_conversation = AsyncMock(return_value="conv-x")

    accs = [_account("r1", 5), _account("r2", 3)]
    pool = _make_pool(accs, adapter_mock=adapter)
    # store.list_all нужен для _earliest_reset_ts
    pool._store.list_all = AsyncMock(return_value=accs)

    raised = False
    try:
        await pool.complete(prompt="Тест", session_id="ses-2")
    except AllAccountsRateLimited as e:
        raised = True
        assert e.next_reset_ts > 0, f"next_reset_ts={e.next_reset_ts}"
    except Exception as e:
        fail("complete: AllAccountsRateLimited", str(e))
        return

    if raised:
        ok("complete: все ответили 429 → AllAccountsRateLimited + next_reset_ts")
    else:
        fail("complete: AllAccountsRateLimited", "исключение не поднялось")

asyncio.run(test_all_rate_limited())


# ── 6. complete: 401 → ротация → следующий аккаунт ──────────────────
print("\n[6] complete: 401 первый → переключается на второй")
async def test_auth_failover():
    calls = []

    async def side_effect(prompt, conversation_id, session_key):
        calls.append(session_key)
        if session_key == "sk_bad":
            raise ClaudeAuthError("401")
        return ClaudeResponse(
            text="Ок!",
            conversation_id=conversation_id,
            message_id="m",
            rate_limit=RateLimitInfo(remaining_requests=50),
        )

    adapter = MagicMock()
    adapter.send_message = AsyncMock(side_effect=side_effect)
    adapter.create_conversation = AsyncMock(return_value="conv-ok")

    acc_bad  = _account("bad",  10); acc_bad.session_key  = "sk_bad"
    acc_good = _account("good", 5);  acc_good.session_key = "sk_good"

    pool = _make_pool([acc_bad, acc_good], adapter_mock=adapter)
    pool._lifecycle.get_active_accounts = AsyncMock(return_value=[acc_bad, acc_good])
    pool._lifecycle.rotate_session_key = AsyncMock(return_value=False)  # ротация не удалась
    pool._store.list_all = AsyncMock(return_value=[acc_bad, acc_good])

    try:
        resp = await pool.complete(prompt="Тест", session_id="ses-3")
        assert resp.text == "Ок!"
        assert "sk_bad" in calls
        assert "sk_good" in calls
        ok("complete: 401 на первом → второй аккаунт ответил")
    except Exception as e:
        fail("complete: 401 failover", str(e))

asyncio.run(test_auth_failover())


# ── 7. AllAccountsRateLimited.next_reset_ts ──────────────────────────────────
print("\n[7] AllAccountsRateLimited.next_reset_ts")
try:
    ts = time.time() + 999
    err = AllAccountsRateLimited(next_reset_ts=ts)
    assert err.next_reset_ts == ts
    assert "999" in str(err) or "ts=" in str(err)
    ok("AllAccountsRateLimited: next_reset_ts пробрасывается в Level 6")
except Exception as e:
    fail("AllAccountsRateLimited.next_reset_ts", str(e))


# ── 8. _earliest_reset_ts ─────────────────────────────────────────────────────
print("\n[8] _earliest_reset_ts")
async def test_earliest_reset():
    ts_early = time.time() + 300
    ts_late  = time.time() + 900
    a1 = _account("x1", 0); a1.rate_limit_reset_ts = ts_early
    a2 = _account("x2", 0); a2.rate_limit_reset_ts = ts_late
    pool = _make_pool([a1, a2])
    pool._store.list_all = AsyncMock(return_value=[a1, a2])
    result = await pool._earliest_reset_ts()
    assert result == ts_early, f"result={result} expected={ts_early}"
    ok("_earliest_reset_ts: возвращает минимальный")

asyncio.run(test_earliest_reset())


# ── результат ────────────────────────────────────────────────────────────
print("\n" + "="*50)
total = len(PASSED) + len(FAILED)
print(f"Результат: {len(PASSED)}/{total} пройдено")
if FAILED:
    for f in FAILED: print(f"  - {f}")
    sys.exit(1)
else:
    print("\n=== ALL ASSERTIONS PASSED ===")
    sys.exit(0)