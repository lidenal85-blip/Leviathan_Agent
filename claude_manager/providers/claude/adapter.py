"""
ClaudeAdapter — HTTP-адаптер для claude.ai (reverse-engineered web API).

Архитектурные принципы:
- Не знает про аккаунты и ротацию — это зона LLMProviderPool
- Принимает session_key как параметр — Pool решает какой использовать
- Возвращает типизированные dataclass — Pool читает rate_limit_info
- Все ошибки типизированы — Pool/LifecycleManager реагирует на тип
- SSE стриминг — опционально через async generator
- Готов к Level 6: replay_messages для миграции между аккаунтами

Схема логирования:
    log.task(...)   → начало крупной операции (лог + TG)
    log.step(...)   → внутренний шаг (только лог)
    log.result(...) → успешный итог (лог + TG)
    log.next(...)   → план (только лог)
    log.error(...)  → ошибка (лог + TG-алерт)
    log.warn(...)   → предупреждение (только лог)
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

from claude_manager.logger import StepLogger

_log = StepLogger("claude_adapter")

# ── константы ────────────────────────────────────────────────────────

CLAUDE_BASE = "https://claude.ai"

# Эндпоинты (реверс-инженеринг claude.ai)
_URL_ORGS         = f"{CLAUDE_BASE}/api/organizations"
_URL_CONVERSATIONS = f"{CLAUDE_BASE}/api/organizations/{{org_id}}/chat_conversations"
_URL_COMPLETION   = f"{CLAUDE_BASE}/api/organizations/{{org_id}}/chat_conversations/{{conv_id}}/completion"

# Заголовки rate limit (совпадают с lifecycle_manager)
HDR_RL_REMAINING = "x-ratelimit-remaining-requests"
HDR_RL_RESET     = "x-ratelimit-reset"
HDR_RL_TOKENS    = "x-ratelimit-remaining-tokens"

# Таймауты
_CONNECT_TIMEOUT  = 10.0   # секунд на подключение
_READ_TIMEOUT     = 60.0   # секунд на первый байт SSE
_SSE_IDLE_TIMEOUT = 120.0  # секунд без данных в SSE потоке

# ── исключения ───────────────────────────────────────────────────────

class ClaudeAdapterError(Exception):
    """Базовый класс — ловить для любой ошибки адаптера."""

class ClaudeAuthError(ClaudeAdapterError):
    """
    401 — session_key устарел или невалиден.
    Pool должен вызвать LifecycleManager.rotate_session_key().
    """

class ClaudeRateLimitError(ClaudeAdapterError):
    """
    429 или rate_remaining == 0.
    Pool должен поставить аккаунт в RATE_LIMITED и взять следующий.
    Level 6: ResumeManager читает next_reset_ts для планирования.
    """
    def __init__(self, message: str, next_reset_ts: float = 0.0):
        super().__init__(message)
        self.next_reset_ts = next_reset_ts  # unix timestamp когда лимит сбросится

class ClaudeTimeoutError(ClaudeAdapterError):
    """Превышен таймаут соединения или SSE потока."""

class ClaudeServerError(ClaudeAdapterError):
    """5xx от claude.ai — временная проблема сервера."""
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code

# ── dataclass результатов ────────────────────────────────────────────

@dataclass
class RateLimitInfo:
    """
    Данные о rate limit из заголовков ответа.
    Pool передаёт в LifecycleManager.report_usage().
    Level 6: ResumeManager читает next_reset_ts.
    """
    remaining_requests: int   = 100
    next_reset_ts:      float = 0.0    # unix timestamp
    remaining_tokens:   int   = 10000

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "RateLimitInfo":
        remaining = int(headers.get(HDR_RL_REMAINING, 100))
        tokens    = int(headers.get(HDR_RL_TOKENS, 10000))
        reset_str = headers.get(HDR_RL_RESET, "0")
        try:
            import dateutil.parser
            reset_ts = dateutil.parser.parse(reset_str).timestamp()
        except Exception:
            try:
                reset_ts = float(reset_str)
            except Exception:
                reset_ts = time.time() + 3600
        return cls(
            remaining_requests=remaining,
            next_reset_ts=reset_ts,
            remaining_tokens=tokens,
        )

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_requests <= 0


@dataclass
class ClaudeMessage:
    """Одно сообщение в истории переписки."""
    role:    str   # "human" | "assistant"
    content: str


@dataclass
class ClaudeResponse:
    """
    Результат send_message / replay_messages.
    Pool использует rate_limit для report_usage().
    """
    text:             str
    conversation_id:  str
    message_id:       str
    rate_limit:       RateLimitInfo = field(default_factory=RateLimitInfo)
    latency_ms:       int = 0
    stop_reason:      str = "end_turn"   # end_turn | max_tokens | stop_sequence

# ── внутренние вспомогательные ──────────────────────────────────────

def _build_headers(session_key: str) -> dict:
    """HTTP заголовки. session_key НЕ попадает в логи."""
    return {
        "cookie":       f"sessionKey={session_key}",
        "user-agent":   "Mozilla/5.0 (compatible; LeviathanAgent/1.0)",
        "accept":       "application/json",
        "content-type": "application/json",
        "origin":       CLAUDE_BASE,
        "referer":      f"{CLAUDE_BASE}/",
    }

def _parse_rate_limit_ts(reset_str: str) -> float:
    """Парсит ISO-8601 или unix timestamp → float."""
    try:
        import dateutil.parser
        return dateutil.parser.parse(reset_str).timestamp()
    except Exception:
        try:
            return float(reset_str)
        except Exception:
            return time.time() + 3600

def _new_uuid() -> str:
    return str(uuid.uuid4())

# ── ClaudeAdapter ───────────────────────────────────────────────────

class ClaudeAdapter:
    """
    Низкоуровневый HTTP-адаптер для claude.ai.

    Использование (через LLMProviderPool):
        adapter = ClaudeAdapter()
        await adapter.init(session_key)      # получаем org_id
        conv_id = await adapter.create_conversation(session_key)
        resp = await adapter.send_message("Привет", conv_id, session_key)
        print(resp.text, resp.rate_limit.remaining_requests)

    Level 6 (миграция при лимите):
        new_conv = await adapter.create_conversation(new_session_key)
        await adapter.replay_messages(history, new_session_key, new_conv)
    """

    def __init__(self, timeout_connect: float = _CONNECT_TIMEOUT, timeout_read: float = _READ_TIMEOUT):
        self._timeout_connect = timeout_connect
        self._timeout_read    = timeout_read
        # кэш org_id по session_key hash (не храним сам ключ)
        self._org_cache: dict[str, str] = {}

    # ── публичный API ───────────────────────────────────────────────

    async def get_org_id(self, session_key: str) -> str:
        """
        Получить organization ID для аккаунта.
        Результат кэшируется (ключ = hash session_key).
        Raises: ClaudeAuthError, ClaudeTimeoutError, ClaudeServerError
        """
        cache_key = str(hash(session_key))
        if cache_key in self._org_cache:
            return self._org_cache[cache_key]

        _log.step("get_org_id: запрос к /api/organizations")
        async with self._client(session_key) as client:
            try:
                resp = await client.get(_URL_ORGS)
            except httpx.TimeoutException as e:
                raise ClaudeTimeoutError(f"get_org_id timeout: {e}") from e

        self._raise_for_status(resp, context="get_org_id")

        data = resp.json()
        if not data or not isinstance(data, list):
            raise ClaudeAuthError("get_org_id: пустой ответ — невалидный session_key")

        org_id = data[0].get("uuid", "")
        if not org_id:
            raise ClaudeAuthError("get_org_id: не найден uuid организации")

        self._org_cache[cache_key] = org_id
        _log.step(f"get_org_id: org_id получен (кэшировано)")
        return org_id

    async def create_conversation(self, session_key: str) -> str:
        """
        Создать новую пустую беседу.
        Возвращает conversation_id (UUID).
        Raises: ClaudeAuthError, ClaudeTimeoutError, ClaudeServerError
        """
        _log.step("create_conversation")
        org_id = await self.get_org_id(session_key)
        url    = _URL_CONVERSATIONS.format(org_id=org_id)
        body   = {"uuid": _new_uuid(), "name": ""}

        async with self._client(session_key) as client:
            try:
                resp = await client.post(url, json=body)
            except httpx.TimeoutException as e:
                raise ClaudeTimeoutError(f"create_conversation timeout: {e}") from e

        self._raise_for_status(resp, context="create_conversation")
        conv_id = resp.json().get("uuid", "")
        if not conv_id:
            raise ClaudeServerError("create_conversation: нет uuid в ответе", resp.status_code)

        _log.step(f"create_conversation: conv_id создан")
        return conv_id

    async def send_message(
        self,
        prompt: str,
        conversation_id: str,
        session_key: str,
        *,
        stream: bool = False,
    ) -> ClaudeResponse:
        """
        Отправить сообщение, получить ответ.

        Args:
            prompt:          текст запроса
            conversation_id: UUID беседы (из create_conversation)
            session_key:     ключ сессии аккаунта
            stream:          True = накапливать SSE (медленнее, но дешевле для длинных ответов)

        Returns:
            ClaudeResponse с text, rate_limit, latency_ms

        Raises:
            ClaudeAuthError, ClaudeRateLimitError, ClaudeTimeoutError, ClaudeServerError
        """
        _log.step(f"send_message: conv_id={conversation_id[:8]}... stream={stream}")
        t0 = time.time()

        org_id = await self.get_org_id(session_key)
        url    = _URL_COMPLETION.format(org_id=org_id, conv_id=conversation_id)
        body   = self._build_completion_body(prompt, conversation_id)

        if stream:
            text, rate_limit, stop_reason = await self._send_streaming(url, body, session_key)
        else:
            text, rate_limit, stop_reason = await self._send_blocking(url, body, session_key)

        latency_ms = int((time.time() - t0) * 1000)
        _log.step(
            f"send_message: ok latency={latency_ms}ms "
            f"remaining={rate_limit.remaining_requests} "
            f"stop={stop_reason}"
        )

        if rate_limit.is_exhausted:
            _log.warn(f"send_message: rate limit исчерпан, next_reset={rate_limit.next_reset_ts}")
            raise ClaudeRateLimitError(
                "rate limit exhausted",
                next_reset_ts=rate_limit.next_reset_ts,
            )

        return ClaudeResponse(
            text=text,
            conversation_id=conversation_id,
            message_id=_new_uuid(),
            rate_limit=rate_limit,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
        )

    async def replay_messages(
        self,
        messages: list[ClaudeMessage],
        session_key: str,
        conversation_id: str,
        *,
        max_messages: int = 20,
    ) -> None:
        """
        Воспроизвести историю сообщений в новой беседе.
        Используется при миграции аккаунта (Level 6: смена аккаунта при лимите).

        Отправляет только human-сообщения — claude генерирует свои ответы.
        Лимит: последние max_messages (MVP ограничение).

        Args:
            messages:        история [{role, content}, ...]
            session_key:     ключ нового аккаунта
            conversation_id: новая беседа (уже создана)
            max_messages:    сколько последних брать (MVP: 20)

        Raises:
            ClaudeAuthError, ClaudeRateLimitError, ClaudeTimeoutError
        """
        _log.task(f"replay_messages: {len(messages)} сообщений → берём последние {max_messages}")

        # берём только human-сообщения (последние N)
        human_msgs = [m for m in messages if m.role == "human"][-max_messages:]
        _log.step(f"replay_messages: {len(human_msgs)} human-сообщений для воспроизведения")

        for i, msg in enumerate(human_msgs):
            _log.step(f"replay_messages: отправка {i+1}/{len(human_msgs)}")
            await self.send_message(msg.content, conversation_id, session_key)
            # небольшая пауза чтобы не триггерить rate limit
            import asyncio
            await asyncio.sleep(0.5)

        _log.result(f"replay_messages: воспроизведено {len(human_msgs)} сообщений")
        _log.next("LLMProviderPool обновит SessionContextManager с новым conversation_id")

    async def stream_message(
        self,
        prompt: str,
        conversation_id: str,
        session_key: str,
    ) -> AsyncIterator[str]:
        """
        SSE стриминг — async generator токенов.
        Для будущего Telegram streaming (не используется в MVP).

        Пример:
            async for chunk in adapter.stream_message(prompt, conv_id, key):
                print(chunk, end="", flush=True)
        """
        org_id = await self.get_org_id(session_key)
        url    = _URL_COMPLETION.format(org_id=org_id, conv_id=conversation_id)
        body   = self._build_completion_body(prompt, conversation_id)

        _log.step(f"stream_message: открываем SSE stream conv_id={conversation_id[:8]}...")

        async with self._client(session_key, stream=True) as client:
            try:
                async with client.stream("POST", url, json=body) as resp:
                    self._raise_for_status(resp, context="stream_message")
                    async for line in resp.aiter_lines():
                        chunk = self._parse_sse_line(line)
                        if chunk is not None:
                            yield chunk
            except httpx.TimeoutException as e:
                raise ClaudeTimeoutError(f"stream_message timeout: {e}") from e

    # ── приватные методы ─────────────────────────────────────────────

    def _client(self, session_key: str, *, stream: bool = False) -> httpx.AsyncClient:
        """Создаёт httpx клиент с нужными заголовками и таймаутами."""
        timeout = httpx.Timeout(
            connect=self._timeout_connect,
            read=_SSE_IDLE_TIMEOUT if stream else self._timeout_read,
            write=30.0,
            pool=5.0,
        )
        return httpx.AsyncClient(
            headers=_build_headers(session_key),
            timeout=timeout,
            follow_redirects=True,
        )

    def _raise_for_status(self, resp: httpx.Response, *, context: str) -> None:
        """
        Превращает HTTP статусы в типизированные исключения.
        Никогда не логирует session_key.
        """
        code = resp.status_code
        if code == 200 or code == 201:
            return
        if code == 401:
            _log.warn(f"{context}: 401 — session_key устарел")
            raise ClaudeAuthError(f"{context}: 401 Unauthorized")
        if code == 429:
            rate = RateLimitInfo.from_headers(resp.headers)
            _log.warn(f"{context}: 429 — rate limit, next_reset={rate.next_reset_ts}")
            raise ClaudeRateLimitError(
                f"{context}: 429 Too Many Requests",
                next_reset_ts=rate.next_reset_ts,
            )
        if code >= 500:
            _log.warn(f"{context}: {code} — ошибка сервера claude.ai")
            raise ClaudeServerError(f"{context}: {code} Server Error", status_code=code)
        # остальные 4xx
        _log.warn(f"{context}: {code} — неожиданный статус")
        raise ClaudeAdapterError(f"{context}: HTTP {code}")

    @staticmethod
    def _build_completion_body(prompt: str, conversation_id: str) -> dict:
        """Тело запроса для /completion эндпоинта."""
        return {
            "prompt": prompt,
            "parent_message_uuid": None,
            "timezone": "Europe/Moscow",
            "personalized_styles": [],
            "tools": [],
            "thinking": {"type": "disabled"},
            "attachments": [],
            "files": [],
            "sync_sources": [],
            "rendering_mode": "raw",
        }

    async def _send_blocking(
        self, url: str, body: dict, session_key: str
    ) -> tuple[str, RateLimitInfo, str]:
        """
        Не-стриминг режим: собираем весь SSE поток и возвращаем финальный текст.
        Возвращает (text, rate_limit_info, stop_reason).
        """
        text        = ""
        rate_limit  = RateLimitInfo()
        stop_reason = "end_turn"

        async with self._client(session_key, stream=True) as client:
            try:
                async with client.stream("POST", url, json=body) as resp:
                    self._raise_for_status(resp, context="send_message")
                    rate_limit = RateLimitInfo.from_headers(resp.headers)

                    async for line in resp.aiter_lines():
                        # парсим SSE
                        chunk = self._parse_sse_line(line)
                        if chunk is not None:
                            text += chunk
                        # ловим stop_reason из event: message_stop
                        stop = self._parse_stop_reason(line)
                        if stop:
                            stop_reason = stop

            except httpx.TimeoutException as e:
                raise ClaudeTimeoutError(f"send_message timeout: {e}") from e

        return text, rate_limit, stop_reason

    async def _send_streaming(
        self, url: str, body: dict, session_key: str
    ) -> tuple[str, RateLimitInfo, str]:
        """Алиас _send_blocking — в текущем MVP логика одинакова."""
        return await self._send_blocking(url, body, session_key)

    @staticmethod
    def _parse_sse_line(line: str) -> Optional[str]:
        """
        Парсит строку SSE и возвращает текстовый чанк или None.

        Форматы claude.ai SSE:
            data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}
            data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}
            data: [DONE]
        """
        if not line.startswith("data: "):
            return None
        raw = line[6:].strip()
        if raw == "[DONE]" or not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None

        event_type = obj.get("type", "")
        if event_type == "content_block_delta":
            delta = obj.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
        return None

    @staticmethod
    def _parse_stop_reason(line: str) -> Optional[str]:
        """Извлекает stop_reason из SSE строки если есть."""
        if not line.startswith("data: "):
            return None
        raw = line[6:].strip()
        try:
            obj = json.loads(raw)
            if obj.get("type") == "message_delta":
                return obj.get("delta", {}).get("stop_reason")
        except (json.JSONDecodeError, AttributeError):
            pass
        return None