"""CryptoKeyManager — единственный источник шифрования.
Читает ключ из файла (400), не из env.
"""
from __future__ import annotations
import os, stat
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from claude_manager.logger import StepLogger

_log = StepLogger("crypto")
DEFAULT_KEY_FILE = "/etc/leviathan/crypto.key"


class CryptoError(Exception):
    pass


class CryptoKeyManager:
    """Singleton. Инициализируется один раз при старте."""
    _instance: "CryptoKeyManager | None" = None
    _fernet: Fernet | None = None

    def __new__(cls, key_file: str = DEFAULT_KEY_FILE) -> "CryptoKeyManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(key_file)
        return cls._instance

    def _init(self, key_file: str) -> None:
        _log.task(f"загрузка ключа шифрования из {key_file}")
        path = Path(key_file)
        if not path.exists():
            raise CryptoError(f"Файл ключа не найден: {key_file}")
        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        if mode != 0o400:
            raise CryptoError(
                f"Небезопасные права файла ключа ({oct(mode)}). "
                f"Исправьте: chmod 400 {key_file}"
            )
        _log.step("валидация Fernet ключа")
        self._fernet = Fernet(path.read_text().strip().encode())
        _log.result("ключ загружен, CryptoKeyManager готов")
        _log.next("шифрование полей AccountStore")

    def encrypt(self, plaintext: str) -> str:
        """str -> base64-ciphertext str"""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """base64-ciphertext str -> str"""
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as e:
            raise CryptoError("Ошибка дешифровки: неверный ключ или повреждённые данные") from e


def get_crypto() -> CryptoKeyManager:
    key_file = os.environ.get("CLAUDE_CRYPTO_KEY_FILE", DEFAULT_KEY_FILE)
    return CryptoKeyManager(key_file)