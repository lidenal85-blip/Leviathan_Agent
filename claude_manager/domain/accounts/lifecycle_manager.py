"""
AccountLifecycleManager — здоровье аккаунтов, ротация сессий, статус для роутера.

MVP-ограничения:
- Без WebSocket (поллинг через get_stats)
- Без CircuitBreaker
- SQLite advisory lock вместо Redis
- Health check = GET /api/organizations с session_key
- Ротация: заголовок + POST credentials → новый session_key

Схема логирования:
    log.task(...)   → начало крупной операции (лог + TG)
    log.step(...)   → внутренний шаг (только лог)
    log.result(...) → успешный итог (лог + TG)
    log.next(...)   → план (только лог)
    log.error(...)  → ошибка (лог + TG-алерт)
    log.warn(...)   → предупреждение (только лог)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from claude_manager.core.storage.account_store import Account, AccountStatus, AccountStore
from claude_manager.core.storage.advisory_lock import AdvisoryLock, LockAcquireError
from claude_manager.logger import StepLogger

_log = StepLogger("accounts")

# ── константы ────────────────────────────────────────────────────────

# Claude.ai эндпоинты (реверс-инженеринг)
CLAUDE_BASE          = "https://claude.ai"
CLAUDE_HEALTH_URL    = f"{CLAUDE_BASE}/api/organizations"  # 200 = активный, 401 = устарел

# заголовки rate limit
HDR_RL_REMAINING = "x-ratelimit-remaining-requests"
HDR_RL_RESET     = "x-ratelimit-reset"
HDR_RL_TOKENS    = "x-ratelimit-remaining-tokens"

MAX_CONSECUTIVE_FAILURES = 3  # после N ошибок → DEAD
HTTP_TIMEOUT             = 20.0  # секунд


# ── dataclass для дашборда ──────────────────────────────────────────

@dataclass
class AccountStat:
    account_id:        str
    email:             str
    status:            str
    rate_remaining:    int
    rate_reset_ts:     float
    consecutive_fails: int
    last_check_ago:    float  # секунд назад
    updated_at:        float


# ── вспомогательные функции ─────────────────────────────────────────

def _build_headers(session_key: str) -> dict:
    """HTTP-заголовки для Claude API. session_key НЕ попадает в логи."""
    return {
        "cookie": f"sessionKey={session_key}",
        "user-agent": "Mozilla/5.0 (compatible; LeviathanAgent)",
        "accept": "application/json",
    }


def _parse_rate_limit(headers: httpx.Headers) -> tuple[int, float]:
    """(остаток запросов, timestamp сброса)."""
    remaining = int(headers.get(HDR_RL_REMAINING, 100))
    reset_str = headers.get(HDR_RL_RESET, "0")
    try:
        # Claude отдаёт ISO-8601 или unix timestamp
        import dateutil.parser
        reset_ts = dateutil.parser.parse(reset_str).timestamp()
    except Exception:
        try:
            reset_ts = float(reset_str)
        except Exception:
            reset_ts = time.time() + 3600  # фолбек: +1ч
    return remaining, reset_ts


# ── AccountLifecycleManager ────────────────────────────────────────────

class AccountLifecycleManager:
    """
    Управляет жизненным циклом аккаунтов Claude:
    - Фоновый scheduler health check (staggered)
    - Ротация session_key по 401 (через advisory lock)
    - Трекинг rate limit из заголовков
    - Статистика для дашборда (поллинг)
    """

    def __init__(
        self,
        store: AccountStore,
        health_interval: int = 300,
        max_concurrent: int = 3,
    ):
        self._store           = store
        self._health_interval = health_interval
        self._max_concurrent  = max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._task:       Optional[asyncio.Task]      = None
        self._running:    bool = False
        # последнее время health check пер аккаунт: {account_id: float}
        self._last_check: dict[str, float] = {}

    # ── жизненный цикл ─────────────────────────────────────────────

    async def start(self) -> None:
        """Start фонового scheduler хеалтчеков."""
        _log.task("запуск AccountLifecycleManager")
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._running   = True
        self._task      = asyncio.create_task(self._scheduler_loop())
        _log.result(
            f"scheduler запущен "
            f"(interval={self._health_interval}s, max_concurrent={self._max_concurrent})"
        )
        _log.next("ClaudeAdapter будет вызывать get_active_accounts")

    async def stop(self) -> None:
        """Graceful shutdown: отменяем scheduler, ждём завершения."""
        _log.task("остановка AccountLifecycleManager")
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log.result("scheduler остановлен")

    # ── публичный API ───────────────────────────────────────────────

    async def add_account(self, email: str, session_key: str, password: str = "") -> str:
        """Добавить аккаунт.
        session_key — из DevTools → Application → Cookies → sessionKey
        password   — опционально, для авто-ротации через Playwright
        """
        _log.task(f"add_account: {email}")
        account_id = await self._store.add(email, session_key, password)
        _log.result(f"add_account: {email} id={account_id}")
        _log.next("scheduler запустит health check на следующем цикле")
        return account_id

    async def update_session_key(self, account_id: str, session_key: str) -> bool:
        """Обновить session_key вручную (если старый истёк)."""
        _log.task(f"update_session_key: acc={account_id}")
        acc = await self._store.get(account_id)
        if not acc:
            _log.warn(f"update_session_key: acc={account_id} не найден")
            return False
        await self._store.update_session_key(account_id, session_key)
        await self._store.update_status(account_id, AccountStatus.ACTIVE)
        _log.result(f"update_session_key: acc={account_id} обновлён, статус → ACTIVE")
        return True

    async def remove_account(self, account_id: str) -> bool:
        _log.task(f"remove_account: {account_id}")
        ok = await self._store.remove(account_id)
        _log.result(f"remove_account: {account_id} → {'OK' if ok else 'not found'}")
        return ok

    async def get_active_accounts(self) -> list[Account]:
        """Список аккаунтов, готовых к работе (не DEAD / AUTH_FAILED / RATE_LIMITED)."""
        all_acc = await self._store.list_all()
        now = time.time()
        active = []
        for acc in all_acc:
            if acc.status in (AccountStatus.DEAD, AccountStatus.AUTH_FAILED, AccountStatus.DECRYPTION_FAILED):
                continue
            if acc.status == AccountStatus.RATE_LIMITED:
                # пропускаем, если rate limit reset ещё не наступил
                if acc.rate_limit_reset_ts > now + 30:
                    continue
            active.append(acc)
        _log.step(f"get_active_accounts: {len(active)}/{len(all_acc)} доступно")
        return active

    async def report_usage(
        self,
        account_id: str,
        tokens_used: int,
        success: bool,
        rate_remaining: Optional[int] = None,
        rate_reset_ts: Optional[float] = None,
    ) -> None:
        """
        Обновляет счётчики после запроса.
        Вызывается LLMProviderPool после каждого запроса к Claude API.
        """
        _log.step(
            f"report_usage: acc={account_id} tokens={tokens_used} "
            f"success={success} remaining={rate_remaining}"
        )
        if not success:
            await self._store.update_status(
                account_id,
                status=AccountStatus.DEGRADED,
                inc_failures=True,
            )
            # проверяем пороговое значение
            acc = await self._store.get(account_id)
            if acc and acc.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                _log.warn(
                    f"acc={account_id} превысил {MAX_CONSECUTIVE_FAILURES} "
                    f"ошибок подряд → DEAD"
                )
                await self._store.update_status(account_id, AccountStatus.DEAD)
        else:
            await self._store.update_status(
                account_id,
                status=AccountStatus.ACTIVE,
                rate_remaining=rate_remaining,
                rate_reset_ts=rate_reset_ts,
            )

    async def rotate_session_key(self, account_id: str) -> bool:
        """
        Ротация session_key через advisory lock.
        Возвращает True если новый ключ получен, False — неудача.
        
        NOTE (MVP): реальный reverse-eng автологин — в ClaudeAdapter.
        Здесь получаем сессию через POST /api/auth (base impl).
        """
        _log.task(f"rotate_session_key: acc={account_id}")
        lock_key = f"rotation_{account_id}"

        try:
            async with AdvisoryLock(self._store.db_path, lock_key, timeout=30):
                _log.step(f"lock захвачен acc={account_id}")
                # перечитываем аккаунт свежими данными
                acc = await self._store.get(account_id)
                if acc is None:
                    _log.warn(f"rotate: acc={account_id} не найден в БД")
                    return False

                # не ротируем терминальные статусы — DEAD выставлен report_usage
                if acc.status == AccountStatus.DEAD:
                    _log.warn(
                        f"rotate: acc={account_id} уже DEAD — ротация пропускается"
                    )
                    return False

                # проверяем текущий ключ (вдруг уже ротировали)
                if acc.session_key:
                    ok = await self._do_health_request(acc.session_key)
                    if ok:
                    	_log.step(
                            f"rotate: acc={account_id} ключ ещё работает — ротация не нужна"
                        )
                    	return True

                # выполняем ротацию
                new_key = await self._do_rotate(acc)
                if new_key:
                    await self._store.update_session_key(account_id, new_key)
                    await self._store.update_status(account_id, AccountStatus.ACTIVE)
                    _log.result(f"rotate: acc={account_id} новый ключ сохранён")
                    _log.next("LLMProviderPool использует аккаунт для запросов")
                    return True
                else:
                    # перечитываем — вдруг стал DEAD пока шла ротация
                    acc_now = await self._store.get(account_id)
                    if acc_now and acc_now.status == AccountStatus.DEAD:
                        _log.warn(
                            f"rotate: acc={account_id} стал DEAD во время ротации — "
                            f"не перезаписываем статус"
                        )
                        return False
                    await self._store.update_status(account_id, AccountStatus.AUTH_FAILED)
                    _log.error(
                        f"rotate: acc={account_id} e={acc.email} "
                        f"AUTH_FAILED — нужно ручное вмешательство"
                    )
                    return False

        except LockAcquireError:
            _log.warn(f"rotate: acc={account_id} ротация уже выполняется (лок timeout)")
            return False

    async def get_stats(self) -> list[AccountStat]:
        """Статус всех аккаунтов для дашборда (поллинг)."""
        all_acc = await self._store.list_all()
        now = time.time()
        stats = [
            AccountStat(
                account_id=acc.account_id,
                email=acc.email,
                status=acc.status.value,
                rate_remaining=acc.rate_limit_remaining,
                rate_reset_ts=acc.rate_limit_reset_ts,
                consecutive_fails=acc.consecutive_failures,
                last_check_ago=now - self._last_check.get(acc.account_id, acc.updated_at),
                updated_at=acc.updated_at,
            )
            for acc in all_acc
        ]
        _log.step(f"get_stats: {len(stats)} аккаунтов")
        return stats

    # ── scheduler ──────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        """Staggered health check: каждый аккаунт проверяется по своему таймеру."""
        _log.step("→ _scheduler_loop стартовал")
        while self._running:
            try:
                await self._run_due_checks()
            except Exception as exc:
                _log.error(f"_scheduler_loop исключение: {exc}")
            await asyncio.sleep(10)  # проверяем каждые 10 секунд, нужно ли запустить чеки

    async def _run_due_checks(self) -> None:
        """Запускаем health check для аккаунтов, у которых прошёл health_interval."""
        accounts = await self._store.list_all()
        if not accounts:
            return

        now = time.time()
        due = [
            acc for acc in accounts
            if now - self._last_check.get(acc.account_id, 0) >= self._health_interval
        ]

        if not due:
            return

        _log.step(f"health check due: {len(due)} аккаунтов")
        tasks = [self._health_check_one(acc.account_id) for acc in due]
        # ограничиваем конкурентность через semaphore внутри _health_check_one
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── health check ──────────────────────────────────────────────

    async def _health_check_one(self, account_id: str) -> None:
        """Health check для одного аккаунта, защищён semaphore."""
        async with self._semaphore:
            self._last_check[account_id] = time.time()
            acc = await self._store.get(account_id)
            if acc is None:
                _log.warn(f"health check: acc={account_id} пропал из БД")
                return

            _log.step(f"health check: {acc.email} (id={account_id})")

            if not acc.session_key:
                _log.warn(f"health check: {acc.email} — session_key пуст, требуется ротация")
                await self.rotate_session_key(account_id)
                return

            try:
                status_code, headers = await self._do_health_request_with_headers(
                    acc.session_key
                )
            except Exception as exc:
                _log.warn(f"health check: {acc.email} — ошибка HTTP: {exc}")
                await self._store.update_status(
                    account_id, AccountStatus.DEGRADED, inc_failures=True
                )
                acc_fresh = await self._store.get(account_id)
                if acc_fresh and acc_fresh.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    _log.error(f"health check: {acc.email} → DEAD (нет связи)")
                    await self._store.update_status(account_id, AccountStatus.DEAD)
                return

            await self._process_health_response(
                account_id, acc.email, status_code, headers
            )

    async def _process_health_response(
        self,
        account_id: str,
        email: str,
        status_code: int,
        headers: httpx.Headers,
    ) -> None:
        if status_code == 200:
            remaining, reset_ts = _parse_rate_limit(headers)
            await self._store.update_status(
                account_id,
                AccountStatus.ACTIVE,
                rate_remaining=remaining,
                rate_reset_ts=reset_ts,
            )
            _log.step(
                f"health check OK: {email} "
                f"remaining={remaining} reset_in={(reset_ts - time.time()):.0f}s"
            )

        elif status_code == 429:
            remaining, reset_ts = _parse_rate_limit(headers)
            await self._store.update_status(
                account_id,
                AccountStatus.RATE_LIMITED,
                rate_remaining=0,
                rate_reset_ts=reset_ts,
            )
            _log.warn(
                f"health check: {email} RATE_LIMITED, "
                f"reset_in={(reset_ts - time.time()):.0f}s"
            )

        elif status_code == 401:
            _log.warn(f"health check: {email} — 401, запуск rotate_session_key")
            asyncio.create_task(self.rotate_session_key(account_id))

        elif status_code in (403, 503):
            _log.warn(f"health check: {email} — {status_code} DEGRADED")
            await self._store.update_status(
                account_id, AccountStatus.DEGRADED, inc_failures=True
            )

        else:
            _log.warn(f"health check: {email} — неожиданный {status_code}")
            await self._store.update_status(
                account_id, AccountStatus.DEGRADED, inc_failures=True
            )

    # ── HTTP helpers (не логируем session_key) ───────────────────────

    async def _do_health_request(self, session_key: str) -> bool:
        """True если ключ валиден (200)."""
        try:
            code, _ = await self._do_health_request_with_headers(session_key)
            return code == 200
        except Exception:
            return False

    async def _do_health_request_with_headers(
        self, session_key: str
    ) -> tuple[int, httpx.Headers]:
        """Возвращает (status_code, headers). session_key НЕ попадает в логи."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(
                CLAUDE_HEALTH_URL,
                headers=_build_headers(session_key),
            )
        return resp.status_code, resp.headers

    async def _do_rotate(
        self, acc: Account
    ) -> Optional[str]:
        """
        Авто-ротация sessionKey через Playwright.
        Требует чтобы acc.password был сохранён при add_account.
        """
        if not acc.password:
            _log.warn(
                f"_do_rotate: {acc.email} — password не сохранён."
                f" Обновите вручную: /claude_key {acc.account_id} <key>"
            )
            return None

        _log.step(f"_do_rotate: запуск Playwright для {acc.email}")
        try:
            from claude_manager.core.auth.claude_login import ClaudeLogin, ClaudeLoginConfig
            login = ClaudeLogin(ClaudeLoginConfig(headless=True))
            result = await login.get_session_key(acc.email, acc.password)
            if result.success and result.session_key:
                _log.result(f"_do_rotate: {acc.email} — sessionKey получен")
                return result.session_key
            else:
                _log.warn(
                    f"_do_rotate: {acc.email} — {result.error}."
                    f" Обновите вручную: /claude_key {acc.account_id} <key>"
                )
                return None
        except Exception as exc:
            _log.error(f"_do_rotate: {acc.email} — Playwright ошибка: {exc}")
            return None
        