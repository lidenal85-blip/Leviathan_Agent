"""
ProjectContext — сборка промпта для агента:
  master_prompt + passport + snap_latest + snap_daily + последние N строк лога
"""
from __future__ import annotations
import os, time
from pathlib import Path
from agent.projects.registry import Project

LOG_TAIL_LINES = 80   # сколько последних строк лога подгружать
LOG_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


def _read(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return default


def _tail(path: str, n: int = LOG_TAIL_LINES) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def build_project_prompt(project: Project, user_request: str) -> str:
    """
    Генерирует полный промпт с контекстом проекта:

    === ПРОЕКТ: <name> ===
    [master_prompt]
    [passport]
    [snap_latest]
    [snap_daily]
    [log_current — последние 80 строк]
    === ЗАПРОС ===
    <user_request>
    """
    parts = [f"=== ПРОЕКТ: {project.emoji} {project.name} ==="]

    mp = _read(project.master_prompt_path)
    if mp:
        parts += ["\n--- Мастер-промпт ---", mp]

    pp = _read(project.passport_path)
    if pp:
        parts += ["\n--- Паспорт проекта ---", pp]
    else:
        parts.append("\n⚠️ Паспорт не сгенерирован. Используй /passport для создания.")

    snap = _read(project.snap_latest_path)
    if snap:
        parts += ["\n--- Последний снапшот ---", snap]

    daily = _read(project.snap_daily_path)
    if daily:
        parts += ["\n--- Снапшот дня ---", daily]

    log = _tail(project.log_path)
    if log:
        parts += [f"\n--- Лог (последние {LOG_TAIL_LINES} строк) ---", log]

    parts += ["\n=== ЗАПРОС ПОЛЬЗОВАТЕЛЯ ===", user_request]
    return "\n".join(parts)


def append_log(project: Project, text: str) -> None:
    """Добавляет запись в log_current.md. При превышении 100MB — обрезает старые 20%."""
    path = Path(project.log_path)
    ts   = time.strftime("%Y-%m-%d %H:%M")
    entry = f"\n[{ts}]\n{text.strip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        # читаем, убираем первые 20%
        content = path.read_text(encoding="utf-8")
        cut     = int(len(content) * 0.2)
        path.write_text(content[cut:], encoding="utf-8")

    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def write_snapshot(project: Project, content: str, kind: str = "latest") -> str:
    """Записывает снапшот. kind: latest | daily | weekly"""
    paths = {
        "latest": project.snap_latest_path,
        "daily":  project.snap_daily_path,
        "weekly": project.snap_weekly_path,
    }
    path = Path(paths.get(kind, project.snap_latest_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    ts   = time.strftime("%Y-%m-%d %H:%M")
    path.write_text(f"# Снапшот [{ts}]\n\n{content.strip()}\n", encoding="utf-8")
    return str(path)


def write_passport(project: Project, content: str) -> str:
    path = Path(project.passport_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip(), encoding="utf-8")
    return str(path)