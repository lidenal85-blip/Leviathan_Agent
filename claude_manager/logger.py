"""
Claude Manager — простое структурированное логирование.

Формат каждого шага:
  [TASK] Получил: <что>
  [STEP] Делаю: <действие>
  [RESULT] Итог: <результат>
  [NEXT] Следующий шаг: <план>
  [ERROR] Ошибка: <описание>
"""
import logging
import sys
from datetime import datetime

_fmt = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_fmt,
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/opt/leviathan_engine/agent_service/logs/claude_manager.log"),
    ],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"claude.{name}")


class StepLogger:
    """Логгер одного рабочего цикла. Использовать через контекст-менеджер."""

    def __init__(self, module: str):
        self._log = get_logger(module)

    def task(self, description: str) -> None:
        self._log.info(f"[TASK] Получил: {description}")

    def step(self, action: str) -> None:
        self._log.info(f"[STEP] Делаю: {action}")

    def result(self, outcome: str) -> None:
        self._log.info(f"[RESULT] Итог: {outcome}")

    def next(self, plan: str) -> None:
        self._log.info(f"[NEXT] Следующий шаг: {plan}")

    def error(self, description: str) -> None:
        self._log.error(f"[ERROR] Ошибка: {description}")

    def warn(self, description: str) -> None:
        self._log.warning(f"[WARN] {description}")
