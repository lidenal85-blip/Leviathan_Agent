"""
execution/result_envelope.py — Структурированный конверт результата.

Все ответы Tools Bridge к Агенту обязаны иметь формат (SAD §3):
  {
    "invocation_id": "uuid",
    "status":        "success|partial|error",
    "data":          {...},
    "error_code":    "TIMEOUT|VALIDATION_FAILED|PERMISSION_DENIED|null",
    "retryable":     true|false
  }

Конверт используется для:
  - журналирования в ExecutionJournal
  - решения о retry в ToolsBridge
  - передачи агенту (в data хранится то, что Gemini получает как tool result)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ResultStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR   = "error"


class ErrorCode(str, Enum):
    TIMEOUT            = "TIMEOUT"
    VALIDATION_FAILED  = "VALIDATION_FAILED"
    PERMISSION_DENIED  = "PERMISSION_DENIED"
    NOT_FOUND          = "NOT_FOUND"
    RATE_LIMITED       = "RATE_LIMITED"
    DUPLICATE_CALL     = "DUPLICATE_CALL"    # идемпотентность: уже выполнено
    INTERNAL           = "INTERNAL"


# Ошибки, после которых retry бессмысленен
_FATAL_CODES = {
    ErrorCode.VALIDATION_FAILED,
    ErrorCode.PERMISSION_DENIED,
    ErrorCode.NOT_FOUND,
    ErrorCode.DUPLICATE_CALL,
}

# Инструменты с побочными эффектами (требуют idempotency check)
MUTABLE_TOOLS = frozenset({
    "bash_tool",
    "write_file",
    "git_commit_push",
    "http_post",
})


@dataclass
class ResultEnvelope:
    invocation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status:        ResultStatus       = ResultStatus.SUCCESS
    data:          dict               = field(default_factory=dict)
    error_code:    Optional[ErrorCode] = None
    retryable:     bool               = False

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "status":        self.status.value,
            "data":          self.data,
            "error_code":    self.error_code.value if self.error_code else None,
            "retryable":     self.retryable,
        }

    @property
    def ok(self) -> bool:
        return self.status == ResultStatus.SUCCESS

    # ── Фабрики ─────────────────────────────────────────────────

    @classmethod
    def success(cls, data: dict, invocation_id: str | None = None) -> "ResultEnvelope":
        return cls(
            invocation_id=invocation_id or str(uuid.uuid4()),
            status=ResultStatus.SUCCESS,
            data=data,
            retryable=False,
        )

    @classmethod
    def from_tool_result(
        cls,
        raw: dict,
        invocation_id: str | None = None,
    ) -> "ResultEnvelope":
        """
        Оборачивает сырой dict инструмента в конверт.
        raw["ok"] → статус; raw["error"] → error_code.
        """
        iid = invocation_id or str(uuid.uuid4())
        if raw.get("ok"):
            return cls(
                invocation_id=iid,
                status=ResultStatus.SUCCESS,
                data=raw,
                retryable=False,
            )
        # Определяем error_code по содержимому
        err_msg = raw.get("error", "")
        if "таймаут" in err_msg.lower() or "timeout" in err_msg.lower():
            code, retry = ErrorCode.TIMEOUT, True
        elif "не найден" in err_msg.lower() or "not found" in err_msg.lower():
            code, retry = ErrorCode.NOT_FOUND, False
        elif "отклонен" in err_msg.lower() or "denied" in err_msg.lower():
            code, retry = ErrorCode.PERMISSION_DENIED, False
        else:
            code, retry = ErrorCode.INTERNAL, True

        return cls(
            invocation_id=iid,
            status=ResultStatus.ERROR,
            data=raw,
            error_code=code,
            retryable=retry,
        )

    @classmethod
    def duplicate(cls, cached_result: dict, invocation_id: str) -> "ResultEnvelope":
        """Идемпотентное повторное выполнение — возвращаем кэш."""
        return cls(
            invocation_id=invocation_id,
            status=ResultStatus.SUCCESS,
            data={**cached_result, "_cached": True},
            error_code=ErrorCode.DUPLICATE_CALL,
            retryable=False,
        )

    @classmethod
    def permission_denied(cls, cmd: str, invocation_id: str | None = None) -> "ResultEnvelope":
        return cls(
            invocation_id=invocation_id or str(uuid.uuid4()),
            status=ResultStatus.ERROR,
            data={"error": f"Операция отклонена PolicyEngine: {cmd[:80]}", "ok": False},
            error_code=ErrorCode.PERMISSION_DENIED,
            retryable=False,
        )
