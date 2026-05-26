"""
Claude Manager — логирование + дублирование в Telegram.

Формат:
  [TASK]   -> в лог + TG
  [STEP]   -> только в лог (не сорить TG)
  [RESULT] -> в лог + TG
  [NEXT]   -> только в лог
  [ERROR]  -> в лог + TG (с алертом)
  [WARN]   -> только в лог
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

# Настройка логера
Path("logs").mkdir(exist_ok=True)
_fmt = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_fmt,
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/claude_manager.log"),
    ],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"claude.{name}")


# ── Telegram нотификации ────────────────────────────────────────

_TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
_TG_CHAT  = os.environ.get("TG_ADMIN_CHAT_ID", "")
_TG_ON    = os.environ.get("CLAUDE_LOG_TG", "1") == "1"  # отключить: CLAUDE_LOG_TG=0


def _tg_send(text: str) -> None:
    """Fire-and-forget: отправка в daemon-потоке, не блокирует основной поток."""
    if not (_TG_ON and _TG_TOKEN and _TG_CHAT):
        return

    def _send():
        try:
            url  = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id":    _TG_CHAT,
                "text":       text,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # никогда не падаем из-за TG

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ── StepLogger ───────────────────────────────────────────────────

class StepLogger:
    """Логгер одного рабочего цикла.

    Использование:
        log = StepLogger("accounts")
        log.task("ротация session_key для acc_123")
        log.step("запрос к Claude /auth")
        log.result("новый ключ получен")
        log.next("обновить статус ACTIVE")
    """

    def __init__(self, module: str):
        self._log = get_logger(module)
        self._mod = module

    def task(self, description: str) -> None:
        """[TASK] → лог + TG"""
        msg = f"[TASK] Получил: {description}"
        self._log.info(msg)
        _tg_send(f"🔧 <b>{self._mod}</b>\n{msg}")

    def step(self, action: str) -> None:
        """[STEP] → только лог"""
        self._log.info(f"[STEP] Делаю: {action}")

    def result(self, outcome: str) -> None:
        """[RESULT] → лог + TG"""
        msg = f"[RESULT] Итог: {outcome}"
        self._log.info(msg)
        _tg_send(f"✅ <b>{self._mod}</b>\n{msg}")

    def next(self, plan: str) -> None:
        """[NEXT] → только лог"""
        self._log.info(f"[NEXT] Следующий шаг: {plan}")

    def error(self, description: str) -> None:
        """[ERROR] → лог + TG с алертом"""
        msg = f"[ERROR] Ошибка: {description}"
        self._log.error(msg)
        _tg_send(f"🚨 <b>{self._mod}</b>\n{msg}")

    def warn(self, description: str) -> None:
        """[WARN] → только лог"""
        self._log.warning(f"[WARN] {description}")