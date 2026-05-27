"""
ProjectRegistry — реестр проектов и активная сессия пользователя.
Хранится в SQLite. Файлы проекта в HQ/projects_v2/<slug>/
"""
from __future__ import annotations
import json, os, sqlite3, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HQ_ROOT  = os.environ.get("HQ_ROOT", "/opt/leviathan_engine/HQ/projects_v2")
DB_PATH  = os.environ.get("PROJECT_REGISTRY_DB", "db/project_registry.db")

KNOWN_PROJECTS = [
    {"slug": "leviathan_agent",  "name": "Leviathan Agent",  "path": "/opt/leviathan_engine/agent_service", "emoji": "🤖"},
    {"slug": "arbitr_cockpit",   "name": "ArbitrCockpit",    "path": "/opt/arbitr_cockpit",                  "emoji": "📊"},
    {"slug": "kinovibe",         "name": "KinoVibe",         "path": "/var/www/kinovibe",                    "emoji": "🎬"},
    {"slug": "voicestudio",      "name": "VoiceStudio",      "path": "/var/www/voicestudio",                 "emoji": "🎤"},
    {"slug": "ai_outreach",      "name": "AI Outreach",      "path": "/opt/ai_outreach",                     "emoji": "📧"},
    {"slug": "orionyx",          "name": "Orionyx",          "path": "/opt/orionyx",                         "emoji": "⭐"},
    {"slug": "citrus_aura",      "name": "CitrusAura",       "path": "/opt/citrus_aura",                     "emoji": "🍊"},
]


@dataclass
class Project:
    slug:    str
    name:    str
    path:    str
    emoji:   str
    hq_dir:  str   # путь к папке в HQ

    @property
    def passport_path(self) -> str:
        return os.path.join(self.hq_dir, "passport.md")

    @property
    def master_prompt_path(self) -> str:
        return os.path.join(self.hq_dir, "master_prompt.md")

    @property
    def log_path(self) -> str:
        return os.path.join(self.hq_dir, "log_current.md")

    @property
    def snap_latest_path(self) -> str:
        return os.path.join(self.hq_dir, "snapshots", "snap_latest.md")

    @property
    def snap_daily_path(self) -> str:
        return os.path.join(self.hq_dir, "snapshots", "snap_daily.md")

    @property
    def snap_weekly_path(self) -> str:
        return os.path.join(self.hq_dir, "snapshots", "snap_weekly.md")


class ProjectRegistry:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                slug       TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                path       TEXT NOT NULL,
                emoji      TEXT DEFAULT '\ud83d\udcc1',
                hq_dir     TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id          TEXT PRIMARY KEY,
                active_project   TEXT DEFAULT NULL,
                step_count       INTEGER DEFAULT 0,
                updated_at       REAL NOT NULL
            )
        """)
        con.commit()
        # засеяем известные проекты
        for p in KNOWN_PROJECTS:
            hq = os.path.join(HQ_ROOT, p["slug"])
            Path(hq, "snapshots").mkdir(parents=True, exist_ok=True)
            con.execute(
                "INSERT OR IGNORE INTO projects (slug,name,path,emoji,hq_dir,created_at) VALUES (?,?,?,?,?,?)",
                (p["slug"], p["name"], p["path"], p["emoji"], hq, time.time()),
            )
        con.commit()
        con.close()

    # ── Проекты ──────────────────────────────────────────────

    def list_projects(self) -> list[Project]:
        con = sqlite3.connect(self.db_path)
        rows = con.execute("SELECT slug,name,path,emoji,hq_dir FROM projects ORDER BY name").fetchall()
        con.close()
        return [Project(slug=r[0], name=r[1], path=r[2], emoji=r[3], hq_dir=r[4]) for r in rows]

    def get(self, slug: str) -> Optional[Project]:
        con = sqlite3.connect(self.db_path)
        r = con.execute("SELECT slug,name,path,emoji,hq_dir FROM projects WHERE slug=?", (slug,)).fetchone()
        con.close()
        return Project(slug=r[0], name=r[1], path=r[2], emoji=r[3], hq_dir=r[4]) if r else None

    def add_project(self, slug: str, name: str, path: str, emoji: str = "📁") -> Project:
        hq = os.path.join(HQ_ROOT, slug)
        Path(hq, "snapshots").mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT OR REPLACE INTO projects (slug,name,path,emoji,hq_dir,created_at) VALUES (?,?,?,?,?,?)",
            (slug, name, path, emoji, hq, time.time()),
        )
        con.commit()
        con.close()
        return Project(slug=slug, name=name, path=path, emoji=emoji, hq_dir=hq)

    # ── Активная сессия пользователя ─────────────────────────────

    def set_active(self, user_id: str, slug: Optional[str]) -> None:
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT OR REPLACE INTO user_sessions (user_id,active_project,step_count,updated_at) VALUES (?,?,0,?)",
            (user_id, slug, time.time()),
        )
        con.commit()
        con.close()

    def get_active(self, user_id: str) -> Optional[str]:
        con = sqlite3.connect(self.db_path)
        r = con.execute("SELECT active_project FROM user_sessions WHERE user_id=?", (user_id,)).fetchone()
        con.close()
        return r[0] if r else None

    def inc_step(self, user_id: str) -> int:
        """Returns new step_count."""
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE user_sessions SET step_count=step_count+1,updated_at=? WHERE user_id=?",
            (time.time(), user_id),
        )
        con.commit()
        r = con.execute("SELECT step_count FROM user_sessions WHERE user_id=?", (user_id,)).fetchone()
        con.close()
        return r[0] if r else 0

    def reset_steps(self, user_id: str) -> None:
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE user_sessions SET step_count=0 WHERE user_id=?", (user_id,))
        con.commit()
        con.close()


_registry: Optional[ProjectRegistry] = None

def get_registry() -> ProjectRegistry:
    global _registry
    if _registry is None:
        _registry = ProjectRegistry()
    return _registry