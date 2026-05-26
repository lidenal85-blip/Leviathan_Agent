"""
w3m_syncer.py — синхронизация sessionKey из Termux-профилей в claude_accounts.db

Схема работы:
  1. Сканируем ~/claude_profiles/profile_01 ... profile_10
  2. Ищем sessionKey в файле cookies каждого профиля
  3. UPSERT в db/claude_accounts.db (по email как ключу)
  4. Каждый профиль — уникальный User-Agent (мобильный)

Формат файла cookies (w3m формат, Netscape HTTP Cookie File):
  # Netscape HTTP Cookie File
  .claude.ai  TRUE  /  TRUE  0  sessionKey  sk-ant-...
  .claude.ai  TRUE  /  TRUE  0  email       user@gmail.com

Или простой файл (key=value по одному на строку):
  sessionKey=sk-ant-...
  email=user@gmail.com

Зapycк:
  python w3m_syncer.py [--profiles-dir ~/claude_profiles] [--db /path/to/claude_accounts.db]
  python w3m_syncer.py --watch   # бесконечный режим опроса
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] w3m_syncer: %(message)s",
)
log = logging.getLogger(__name__)

# Значения по умолчанию
DEFAULT_PROFILES_DIR = Path.home() / "claude_profiles"
DEFAULT_DB_PATH      = Path(__file__).parent.parent / "db" / "claude_accounts.db"
PROFILE_NAMES        = [f"profile_{i:02d}" for i in range(1, 11)]
COOKIE_FILES         = ["cookies", "cookies.txt", ".cookies", "w3m_cookies",
                         "session.txt", "session"]
WATCH_INTERVAL_S     = 60   # секунд между циклами в --watch режиме

# Мобильные User-Agent по индексу профиля (0-9)
_MOBILE_UA_POOL = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; OnePlus 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Xiaomi 13 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; Huawei P50 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7a) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Samsung Galaxy A54) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]


def get_user_agent(profile_index: int) -> str:
    """Уникальный User-Agent по индексу профиля (0..9)."""
    return _MOBILE_UA_POOL[profile_index % len(_MOBILE_UA_POOL)]


# ────────────────────────────────────────────────────────────────────────────────
# Парсинг cookies
# ────────────────────────────────────────────────────────────────────────────────

def _parse_netscape_cookies(text: str) -> dict[str, str]:
    """Парсинг Netscape HTTP Cookie File.
    Формат: domain\tflag\tpath\tsecure\texpiry\tname\tvalue
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5], parts[6]
            result[name] = value
    return result


def _parse_keyvalue_cookies(text: str) -> dict[str, str]:
    """key=value формат, по одному на строку."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def parse_profile_cookies(profile_dir: Path) -> Optional[dict[str, str]]:
    """Читает cookie-файл из директории профиля, возвращает dict или None."""
    for fname in COOKIE_FILES:
        fpath = profile_dir / fname
        if fpath.exists():
            text = fpath.read_text(errors="replace")
            # Автоопределение формата
            if "\t" in text and "claude.ai" in text:
                cookies = _parse_netscape_cookies(text)
            else:
                cookies = _parse_keyvalue_cookies(text)
            if cookies.get("sessionKey"):
                log.debug("  прочитан: %s", fpath)
                return cookies
    return None


# ────────────────────────────────────────────────────────────────────────────────
# DB: UPSERT
# ────────────────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS claude_accounts (
    account_id        TEXT PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    enc_password      TEXT DEFAULT '',
    enc_session_key   TEXT DEFAULT '',
    status            TEXT DEFAULT 'ACTIVE',
    rate_limit_remaining INTEGER DEFAULT 100,
    consecutive_failures INTEGER DEFAULT 0,
    user_agent        TEXT DEFAULT '',
    profile_index     INTEGER DEFAULT -1,
    created_at        REAL,
    updated_at        REAL
);
"""


async def ensure_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(CREATE_TABLE_SQL)
    await db.commit()
    # Миграция: добавляем новые колонки если они отсутствуют
    for col, coldef in [
        ("user_agent",    "TEXT DEFAULT ''"),
        ("profile_index", "INTEGER DEFAULT -1"),
    ]:
        try:
            await db.execute(f"ALTER TABLE claude_accounts ADD COLUMN {col} {coldef}")
            await db.commit()
        except Exception:
            pass   # уже есть


async def upsert_account(
    db: aiosqlite.Connection,
    email: str,
    session_key: str,
    profile_index: int,
    dry_run: bool = False,
) -> str:
    """
    UPSERT по email.
    - Если аккаунт существует — обновляем enc_session_key, status, user_agent, updated_at.
    - Если нет — вставляем новый запись.
    Возвращает account_id.
    """
    now        = time.time()
    ua         = get_user_agent(profile_index)
    # Записываем ключ как raw-строку (здесь нет CryptoKeyManager-а, синхер работает автономно)
    # AccountStore при чтении сам расшифрует; если нужно шифрование — включи ENCRYPT=1
    enc_key = session_key   # raw-мод по умолчанию

    async with db.execute(
        "SELECT account_id FROM claude_accounts WHERE email = ?", (email,)
    ) as cur:
        row = await cur.fetchone()

    if row:
        account_id = row[0]
        if not dry_run:
            await db.execute(
                """
                UPDATE claude_accounts
                SET enc_session_key = ?,
                    status          = 'ACTIVE',
                    user_agent      = ?,
                    profile_index   = ?,
                    updated_at      = ?,
                    consecutive_failures = 0
                WHERE email = ?
                """,
                (enc_key, ua, profile_index, now, email),
            )
        log.info("⬆️  обновлён: %s (profile_%02d) id=%s", email, profile_index + 1, account_id)
    else:
        account_id = str(uuid.uuid4())[:8]
        if not dry_run:
            await db.execute(
                """
                INSERT INTO claude_accounts
                    (account_id, email, enc_session_key, status,
                     user_agent, profile_index, created_at, updated_at)
                VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)
                """,
                (account_id, email, enc_key, ua, profile_index, now, now),
            )
        log.info("➕ добавлен:  %s (profile_%02d) id=%s", email, profile_index + 1, account_id)

    if not dry_run:
        await db.commit()
    return account_id


# ────────────────────────────────────────────────────────────────────────────────
# Основной синхронизатор
# ────────────────────────────────────────────────────────────────────────────────

async def sync_once(
    profiles_dir: Path,
    db_path: Path,
    dry_run: bool = False,
) -> dict:
    """Один цикл синхронизации. Возвращает статистику."""
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not profiles_dir.exists():
        log.warning("Папка профилей не найдена: %s", profiles_dir)
        return stats

    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as db:
        await ensure_schema(db)

        for idx, name in enumerate(PROFILE_NAMES):
            profile_dir = profiles_dir / name
            if not profile_dir.exists():
                continue

            stats["scanned"] += 1
            log.debug("скан: %s", profile_dir)

            try:
                cookies = parse_profile_cookies(profile_dir)
            except Exception as exc:
                log.error("ошибка чтения %s: %s", profile_dir, exc)
                stats["errors"] += 1
                continue

            if not cookies:
                log.debug("пропуск (%s): sessionKey не найден", name)
                stats["skipped"] += 1
                continue

            session_key = cookies["sessionKey"]
            email       = cookies.get("email", f"profile_{idx+1:02d}@device.local")

            try:
                await upsert_account(db, email, session_key, idx, dry_run=dry_run)
                stats["updated"] += 1
            except Exception as exc:
                log.error("ошибка upsert %s: %s", email, exc)
                stats["errors"] += 1

    return stats


async def watch(
    profiles_dir: Path,
    db_path: Path,
    interval: int = WATCH_INTERVAL_S,
) -> None:
    """Бесконечный режим: синхронизация каждые N секунд."""
    log.info("режим watch: интервал %ds, profiles=%s, db=%s", interval, profiles_dir, db_path)
    while True:
        try:
            stats = await sync_once(profiles_dir, db_path)
            log.info(
                "цикл: scanned=%d updated=%d skipped=%d errors=%d",
                stats["scanned"], stats["updated"], stats["skipped"], stats["errors"],
            )
        except Exception as exc:
            log.error("неожиданная ошибка: %s", exc)
        await asyncio.sleep(interval)


# ────────────────────────────────────────────────────────────────────────────────
# Точка входа
# ────────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="w3m_syncer: Termux cookies → claude_accounts.db")
    p.add_argument("--profiles-dir", default=str(DEFAULT_PROFILES_DIR),
                   help="Папка с профилями (default: ~/claude_profiles)")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH),
                   help="Путь к db/claude_accounts.db")
    p.add_argument("--watch", action="store_true",
                   help="Бесконечный режим опроса")
    p.add_argument("--interval", type=int, default=WATCH_INTERVAL_S,
                   help="Интервал в секундах (только для --watch)")
    p.add_argument("--dry-run", action="store_true",
                   help="Проверить без записи в БД")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG лог")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    profiles_dir = Path(args.profiles_dir).expanduser()
    db_path      = Path(args.db).expanduser()

    async def _main() -> None:
        if args.watch:
            await watch(profiles_dir, db_path, interval=args.interval)
        else:
            stats = await sync_once(profiles_dir, db_path, dry_run=args.dry_run)
            print(f"Готово: просканировано={stats['scanned']} "
                  f"обновлено={stats['updated']} "
                  f"пропущено={stats['skipped']} "
                  f"ошибок={stats['errors']}")

    asyncio.run(_main())