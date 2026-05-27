"""
execution/pipeline_log.py — Дедикированный лог событий пайплайна.

Формат строки:
  [2026-05-28 12:00:00] TASK_ID | EVENT | деталь

События:
  429_CAUGHT       — получен 429 от LLM-провайдера
  BACKOFF_START    — запущен таймер (часы)
  GATE_PING        — проверочный ping API
  GATE_OK          — API ответил 200 — возобновляемся
  GATE_BLOCKED     — API ещё недоступен — продляем сон
  TASK_PAUSED      — задача переведена в PAUSED
  TASK_RESUMED     — задача возобновлена с шага N
  TASK_COMPLETE    — задача завершена
  TASK_FAILED      — задача упала
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_PIPELINE_LOG_PATH = Path("logs/pipeline.log")
_pipeline_logger: Optional[logging.Logger] = None


def _get_logger() -> logging.Logger:
    global _pipeline_logger
    if _pipeline_logger is not None:
        return _pipeline_logger

    _PIPELINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # не пробрасывать в root logger

    handler = logging.FileHandler(_PIPELINE_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _pipeline_logger = logger
    return logger


def plog(task_id: str, event: str, detail: str = "") -> None:
    """Запись события в pipeline.log."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {task_id:<10} | {event:<16} | {detail}"
    _get_logger().info(line)


class PipelineEvent:
    """Constants для имён событий."""
    CAUGHT_429    = "429_CAUGHT"
    BACKOFF_START = "BACKOFF_START"
    GATE_PING     = "GATE_PING"
    GATE_OK       = "GATE_OK"
    GATE_BLOCKED  = "GATE_BLOCKED"
    TASK_PAUSED   = "TASK_PAUSED"
    TASK_RESUMED  = "TASK_RESUMED"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_FAILED   = "TASK_FAILED"
    HOT_RESUME    = "HOT_RESUME"