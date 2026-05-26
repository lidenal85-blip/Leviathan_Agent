"""
LLMProviderPool — балансировщик между аккаунтами Claude.

Ответственность:
- Выбрать активный аккаунт (round-robin по rate_remaining)
- Вызвать ClaudeAdapter
- Передать результат rate_limit в LifecycleManager.report_usage()
- При ошибках: 401 → rotate, 429 → пометить RATE_LIMITED + следующий аккаунт
- Если ВСЕ аккаунты исчерпаны → AllAccountsRateLimited(next_reset_ts)
  Level 6: ResumeManager ловит это и ставит таймер

НЕ знает про:
- Ротацию внутри аккаунта (это LifecycleManager)
- Историю сессий (это SessionContextManager)
- Шаги проекта (это ProjectExecutor / Level 6)

Схема логирования:
    log.task(...)   → начало крупной операции (лог + TG)
    log.step(...)   → внутренний шаг (только лог)
    log.result(...) → успешный итог (лог + TG)
    log.next(...)   → план (только лог)
    log.error(...)  → ошибка (лог + TG-алерт)
    log.warn(...)   → предупреждение (только лог)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from claude_manager.core.storage.account_store import Account, AccountStatus, AccountStore
from claude_manager.domain.accounts.lifecycle_manager import AccountLifecycleManager
from claude_manager.domain.sessions.context_manager import SessionContextManager
from claude_manager.logger import StepLogger
from claude_manager.providers.claude.adapter import (
    ClaudeAdapter,
    ClaudeAuthError,
    ClaudeMessage,
    ClaudeRateLimitError,
    ClaudeResponse,
    ClaudeTimeoutError,
    ClaudeServerError,
)

_log = StepLogger("llm_pool")

# Максимум попыток переключения аккаунтов за один вызов
_MAX_RETRIES = 3


# ── исключения ────────────────────────────────────────────────────────────────

class AllAccountsRateLimited(Exception):
    """
    Все аккаунты исчерпали лимит.
    Level 6: ResumeManager ловит это исключение и планирует resume.

    Атрибуты:
        next_reset_ts: float — unix timestamp когда ближайший аккаунт освободится.
                               ResumeManager использует это для asyncio.sleep().
    """
    def __init__(self, next_reset_ts: float = 0.0):
        self.next_reset_ts = next_reset_ts
        wait = max(0, next_reset_ts - time.time())
        super().__init__(
            f"All accounts rate limited. Next reset in {wait:.0f}s "
            f"(ts={next_reset_ts:.0f})"
        )


class NoAccountsAvailable(Exception):
    """Нет ни одного аккаунта (не добавлены или все DEAD/AUTH_FAILED)."""


# ── dataclass результата ──────────────────────────────────────────────────────

@dataclass
class PoolResponse:
    """
    Обёртка над ClaudeResponse с информацией об использованном аккаунте.
    SessionContextManager и ProjectExecutor работают с этим объектом.
    """
    text:            str
    conversation_id: str
    account_id:      str       # какой аккаунт ответил
    rate_remaining:  int       # остаток запросов (для мониторинга)
    next_reset_ts:   float     # когда сбросится лимит
    latency_ms:      int
    stop_reason:     str = "end_turn"


# ── LLMProviderPool ───────────────────────────────────────────────────────────

class LLMProviderPool:
    """
    Балансировщик запросов по пулу аккаунтов Claude.

    Использование:
        pool = LLMProviderPool(store, lifecycle, sessions, adapter)
        await pool.init()

        # простой запрос
        resp = await pool.complete(prompt="Привет", session_id="user-123")
        print(resp.text)

        # с существующей сессией (контекст сохранён)
        resp2 = await pool.complete("Продолжай", session_id="user-123")

        # Level 6: ProjectExecutor вызывает так же
        # При AllAccountsRateLimited — ResumeManager ловит и ставит таймер
    """

    def __init__(
        self,
        store:     AccountStore,
        lifecycle: AccountLifecycleManager,
        sessions:  SessionContextManager,
        adapter:   Optional[ClaudeAdapter] = None,
    ):
        self._store     = store
        self._lifecycle = lifecycle
        self._sessions  = sessions
        self._adapter   = adapter or ClaudeAdapter()
        # round-robin курсор: account_id → индекс последнего использованного
        self._rr_index: int = 0

    async def init(self) -> None:
        _log.task("инициализация LLMProviderPool")
        await self._store.init()
        await self._sessions.init()
        await self._lifecycle.start()
        _log.result("LLMProviderPool готов")
        _log.next("deliver/telegram вызывает pool.complete()")

    async def shutdown(self) -> None:
        _log.task("остановка LLMProviderPool")
        await self._lifecycle.stop()
        _log.result("LLMProviderPool остановлен")

    # ── основной метод ────────────────────────────────────────────────────────

    async def complete(
        self,
        prompt:     str,
        session_id: str,
        user_id:    str = "default",
    ) -> PoolResponse:
        """
        Отправить сообщение. Автоматически:
        - Выбирает аккаунт с наибольшим rate_remaining
        - Создаёт/находит conversation в Claude
        - При 401 → ротирует session_key и повторяет
        - При 429 → переключается на следующий аккаунт
        - При всех аккаунтах в лимите → AllAccountsRateLimited

        Args:
            prompt:     текст запроса
            session_id: ID виртуальной сессии пользователя
            user_id:    ID пользователя (для создания новой сессии)

        Returns:
            PoolResponse

        Raises:
            AllAccountsRateLimited: Level 6 ResumeManager ловит это
            NoAccountsAvailable:    нет активных аккаунтов вообще
        """
        _log.task(f"complete: session={session_id[:8]}")

        # Получить или создать маппинг сессии
        mapping = await self._sessions.get_mapping(session_id)
        if mapping is None:
            _log.step("сессия не найдена — создаём новую")
            session_id = await self._sessions.create_session(
                user_id=user_id, provider="claude"
            )
            mapping = await self._sessions.get_mapping(session_id)

        # Попытки с переключением аккаунтов
        tried: set[str] = set()
        last_rate_error: Optional[ClaudeRateLimitError] = None

        for attempt in range(_MAX_RETRIES):
            _log.step(f"complete: попытка {attempt+1}/{_MAX_RETRIES}")

            # Выбрать аккаунт
            account = await self._pick_account(exclude=tried)
            if account is None:
                # все попробованы или нет активных
                if last_rate_error is not None:
                    # вычислить ближайший reset среди всех аккаунтов
                    next_ts = await self._earliest_reset_ts()
                    _log.warn(
                        f"complete: все аккаунты исчерпаны, "
                        f"next_reset в {max(0, next_ts - time.time()):.0f}с"
                    )
                    raise AllAccountsRateLimited(next_reset_ts=next_ts)
                raise NoAccountsAvailable("нет активных аккаунтов")

            tried.add(account.account_id)
            _log.step(f"complete: используем acc={account.account_id}")

            try:
                resp = await self._execute(
                    prompt=prompt,
                    account=account,
                    mapping=mapping,
                    session_id=session_id,
                )
                # Успех — обновляем stats
                await self._lifecycle.report_usage(
                    account_id=account.account_id,
                    tokens_used=len(prompt.split()),   # приблизительно
                    success=True,
                    rate_remaining=resp.rate_limit.remaining_requests,
                    rate_reset_ts=resp.rate_limit.next_reset_ts,
                )
                _log.result(
                    f"complete: ответ получен "
                    f"acc={account.account_id} "
                    f"latency={resp.latency_ms}ms "
                    f"remaining={resp.rate_limit.remaining_requests}"
                )
                return PoolResponse(
                    text=resp.text,
                    conversation_id=resp.conversation_id,
                    account_id=account.account_id,
                    rate_remaining=resp.rate_limit.remaining_requests,
                    next_reset_ts=resp.rate_limit.next_reset_ts,
                    latency_ms=resp.latency_ms,
                    stop_reason=resp.stop_reason,
                )

            except ClaudeAuthError:
                _log.warn(f"complete: 401 на acc={account.account_id} — пробуем ротацию")
                rotated = await self._lifecycle.rotate_session_key(account.account_id)
                if not rotated:
                    _log.warn(f"complete: ротация не удалась, помечаем AUTH_FAILED")
                    await self._store.update_status(
                        account.account_id, AccountStatus.AUTH_FAILED
                    )
                # продолжаем к следующей попытке / следующему аккаунту
                continue

            except ClaudeRateLimitError as e:
                _log.warn(
                    f"complete: 429 на acc={account.account_id} "
                    f"next_reset={e.next_reset_ts:.0f}"
                )
                last_rate_error = e
                # помечаем аккаунт как RATE_LIMITED
                await self._store.update_status(
                    account.account_id,
                    AccountStatus.RATE_LIMITED,
                )
                await self._lifecycle.report_usage(
                    account_id=account.account_id,
                    tokens_used=0,
                    success=False,
                    rate_remaining=0,
                    rate_reset_ts=e.next_reset_ts,
                )
                # продолжаем — попробуем другой аккаунт
                continue

            except ClaudeTimeoutError as e:
                _log.warn(f"complete: timeout на acc={account.account_id}: {e}")
                await self._lifecycle.report_usage(
                    account_id=account.account_id,
                    tokens_used=0,
                    success=False,
                )
                continue

            except ClaudeServerError as e:
                _log.warn(
                    f"complete: сервер вернул {e.status_code} "
                    f"acc={account.account_id}"
                )
                # 5xx — не вина аккаунта, прерываем retry
                raise

        # Исчерпали все попытки
        if last_rate_error:
            next_ts = await self._earliest_reset_ts()
            raise AllAccountsRateLimited(next_reset_ts=next_ts)
        raise NoAccountsAvailable("не удалось получить ответ после всех попыток")

    async def migrate_session(
        self,
        session_id: str,
        history:    list[ClaudeMessage],
    ) -> str:
        """
        Перенести сессию на другой аккаунт (используется Level 6 ResumeManager).

        Создаёт новую conversation на свободном аккаунте,
        воспроизводит историю через adapter.replay_messages(),
        обновляет маппинг сессии.

        Returns:
            account_id нового аккаунта

        Raises:
            AllAccountsRateLimited, NoAccountsAvailable
        """
        _log.task(f"migrate_session: session={session_id[:8]} history={len(history)}")

        account = await self._pick_account()
        if account is None:
            raise NoAccountsAvailable("нет аккаунтов для миграции")

        _log.step(f"migrate_session: новый аккаунт acc={account.account_id}")
        new_conv_id = await self._adapter.create_conversation(account.session_key)

        if history:
            _log.step(f"migrate_session: replay {len(history)} сообщений")
            await self._adapter.replay_messages(
                messages=history,
                session_key=account.session_key,
                conversation_id=new_conv_id,
            )

        await self._sessions.update_mapping(
            session_id=session_id,
            account_id=account.account_id,
            conversation_id=new_conv_id,
            provider="claude",
        )

        _log.result(
            f"migrate_session: перенесено на acc={account.account_id} "
            f"new_conv={new_conv_id[:8]}"
        )
        _log.next("Level 6 ResumeManager продолжает проект на новом аккаунте")
        return account.account_id

    # ── вспомогательные методы ────────────────────────────────────────────────

    async def _execute(
        self,
        prompt:     str,
        account:    Account,
        mapping:    object,
        session_id: str,
    ) -> ClaudeResponse:
        """
        Выполнить запрос: получить/создать conversation, вызвать adapter.
        Обновляет маппинг сессии если conversation создан впервые.
        """
        conv_id = mapping.conversation_id if mapping else ""

        if not conv_id:
            _log.step(f"_execute: conversation не задан — создаём")
            conv_id = await self._adapter.create_conversation(account.session_key)
            await self._sessions.update_mapping(
                session_id=session_id,
                account_id=account.account_id,
                conversation_id=conv_id,
            )

        return await self._adapter.send_message(
            prompt=prompt,
            conversation_id=conv_id,
            session_key=account.session_key,
        )

    async def _pick_account(self, exclude: set[str] = None) -> Optional[Account]:
        """
        Выбрать аккаунт с наибольшим rate_remaining.
        exclude: набор account_id которые уже пробовали в этом вызове.
        """
        exclude = exclude or set()
        candidates = await self._lifecycle.get_active_accounts()
        candidates = [a for a in candidates if a.account_id not in exclude]

        if not candidates:
            return None

        # Сортируем: сначала те у кого больше remaining запросов
        candidates.sort(key=lambda a: a.rate_limit_remaining, reverse=True)
        return candidates[0]

    async def _earliest_reset_ts(self) -> float:
        """
        Найти самый ранний reset_ts среди ВСЕХ аккаунтов.
        Level 6 ResumeManager использует это для таймера.
        """
        all_accounts = await self._store.list_all()
        reset_times = [
            a.rate_limit_reset_ts
            for a in all_accounts
            if a.rate_limit_reset_ts > time.time()
        ]
        if not reset_times:
            # данных нет — ждём час
            return time.time() + 3600
        return min(reset_times)