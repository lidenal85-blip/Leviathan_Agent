"""AccountStore — хранение аккаунтов Claude с field-level шифрованием."""
from __future__ import annotations
import uuid, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import aiosqlite
from claude_manager.logger import StepLogger
from claude_manager.core.crypto.key_manager import get_crypto, CryptoError

_log = StepLogger("account_store")
DB_PATH = "db/claude_accounts.db"


class AccountStatus(str, Enum):
    ACTIVE            = "ACTIVE"
    DEGRADED          = "DEGRADED"
    DEAD              = "DEAD"
    AUTH_FAILED       = "AUTH_FAILED"
    RATE_LIMITED      = "RATE_LIMITED"
    DECRYPTION_FAILED = "DECRYPTION_FAILED"


@dataclass
class Account:
    account_id:           str
    email:                str
    password:             str   # plaintext — дешифруется при чтении
    session_key:          str   # plaintext — дешифруется при чтении
    status:               AccountStatus = AccountStatus.ACTIVE
    rate_limit_remaining: int   = 100
    rate_limit_reset_ts:  float = 0.0
    consecutive_failures: int   = 0
    created_at:           float = field(default_factory=time.time)
    updated_at:           float = field(default_factory=time.time)


class AccountStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._crypto = get_crypto()

    async def init(self) -> None:
        _log.task("инициализация AccountStore")
        _log.step("создание таблицы claude_accounts")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS claude_accounts (
                    account_id            TEXT PRIMARY KEY,
                    email                 TEXT NOT NULL,
                    enc_password          TEXT NOT NULL,
                    enc_session_key       TEXT NOT NULL DEFAULT '',
                    status                TEXT NOT NULL DEFAULT 'ACTIVE',
                    rate_limit_remaining  INTEGER DEFAULT 100,
                    rate_limit_reset_ts   REAL    DEFAULT 0,
                    consecutive_failures  INTEGER DEFAULT 0,
                    created_at            REAL    NOT NULL,
                    updated_at            REAL    NOT NULL
                )
            """)
            await db.commit()
        _log.result("AccountStore готов")
        _log.next("AccountLifecycleManager запускает health checks")

    async def add(self, email: str, session_key: str, password: str = "") -> str:
        """Добавить аккаунт. session_key берётся из браузера (cookies → sessionKey).
        password опциональный — зарезервирован на будущее.
        """
        _log.task(f"добавление аккаунта {email}")
        account_id = str(uuid.uuid4())[:8]
        enc_pw  = self._crypto.encrypt(password) if password else ""
        enc_key = self._crypto.encrypt(session_key) if session_key else ""
        status  = AccountStatus.ACTIVE if session_key else "NEEDS_KEY"
        now = time.time()
        _log.step(f"запись в БД, account_id={account_id}, status={status}")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO claude_accounts
                   (account_id,email,enc_password,enc_session_key,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (account_id, email, enc_pw, enc_key, status, now, now),
            )
            await db.commit()
        _log.result(f"аккаунт {email} добавлен id={account_id} status={status}")
        _log.next("health check запустится на следующем цикле scheduler-а")
        return account_id

    async def get(self, account_id: str) -> Optional[Account]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM claude_accounts WHERE account_id=?", (account_id,)
            ) as cur:
                row = await cur.fetchone()
        return self._to_account(row) if row else None

    async def list_all(self) -> list[Account]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM claude_accounts ORDER BY created_at"
            ) as cur:
                rows = await cur.fetchall()
        return [self._to_account(r) for r in rows]

    async def update_session_key(self, account_id: str, session_key: str) -> None:
        _log.task(f"обновление session_key для {account_id}")
        enc = self._crypto.encrypt(session_key)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE claude_accounts SET enc_session_key=?,updated_at=? WHERE account_id=?",
                (enc, time.time(), account_id),
            )
            await db.commit()
        _log.result(f"session_key обновлён для {account_id}")

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        rate_remaining: Optional[int] = None,
        rate_reset_ts: Optional[float] = None,
        inc_failures: bool = False,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            if inc_failures:
                await db.execute(
                    """UPDATE claude_accounts
                       SET status=?,consecutive_failures=consecutive_failures+1,updated_at=?
                       WHERE account_id=?""",
                    (status, now, account_id),
                )
            else:
                await db.execute(
                    """UPDATE claude_accounts
                       SET status=?,rate_limit_remaining=?,rate_limit_reset_ts=?,
                           consecutive_failures=0,updated_at=?
                       WHERE account_id=?""",
                    (status, rate_remaining or 100, rate_reset_ts or 0.0, now, account_id),
                )
            await db.commit()

    async def remove(self, account_id: str) -> bool:
        _log.task(f"удаление аккаунта {account_id}")
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM claude_accounts WHERE account_id=?", (account_id,)
            )
            await db.commit()
        ok = cur.rowcount > 0
        _log.result(f"{account_id}: {'удалён' if ok else 'не найден'}")
        return ok

    def _to_account(self, row) -> Account:
        try:
            pw = self._crypto.decrypt(row["enc_password"])
            sk = self._crypto.decrypt(row["enc_session_key"]) if row["enc_session_key"] else ""
        except CryptoError:
            _log.error(f"ошибка дешифровки {row['account_id']} — DECRYPTION_FAILED")
            return Account(
                account_id=row["account_id"], email=row["email"],
                password="", session_key="",
                status=AccountStatus.DECRYPTION_FAILED,
            )
        return Account(
            account_id=row["account_id"],
            email=row["email"],
            password=pw,
            session_key=sk,
            status=AccountStatus(row["status"]),
            rate_limit_remaining=row["rate_limit_remaining"],
            rate_limit_reset_ts=row["rate_limit_reset_ts"],
            consecutive_failures=row["consecutive_failures"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )