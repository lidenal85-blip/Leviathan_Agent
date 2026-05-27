"""
tools_file.py — инструменты для работы с файлами в TG.

Предоставляет:
  send_file_to_tg(path)  — отправить файл документом через TG Bot API
  write_and_send(name, content) — записать временный файл, отправить, удалить
"""
from __future__ import annotations

import os
import tempfile
import urllib.request

_TOKEN   = os.environ.get("TG_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TG_ADMIN_CHAT_ID", "")

TOOL_DEFINITIONS = [
    {
        "name": "send_file_to_tg",
        "description": (
            "Отправить файл документом в Telegram админу. "
            "Используй когда пользователь просит прислать файл, отчёт, лог или документ по результатам задачи."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Путь к файлу на сервере"
                },
                "caption": {
                    "type": "string",
                    "description": "Подпись к файлу (необязательно)"
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_and_send_tg",
        "description": (
            "Создать файл с заданным содержимым и отправить его в Telegram. "
            "Используй для генерации отчётов, маркдаун документов и присылки по запросу пользователя."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Имя файла, например report.md или result.txt"
                },
                "content": {
                    "type": "string",
                    "description": "Текстовое содержимое файла"
                },
                "caption": {
                    "type": "string",
                    "description": "Подпись к файлу"
                },
            },
            "required": ["filename", "content"],
        },
    },
]


def send_file_to_tg(file_path: str, caption: str = "") -> str:
    """sendDocument через Bot API. Возвращает 'ok' или описание ошибки."""
    if not (_TOKEN and _CHAT_ID):
        return "error: TG_BOT_TOKEN или TG_ADMIN_CHAT_ID не заданы"
    path = os.path.abspath(file_path)
    if not os.path.exists(path):
        return f"error: файл не найден: {path}"

    import mimetypes
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    boundary = "LeviathanBoundary"
    fname    = os.path.basename(path)

    with open(path, "rb") as f:
        file_data = f.read()

    body  = f"--{boundary}\r\n"
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{_CHAT_ID}\r\n'
    body  = body.encode()
    if caption:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{fname}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()

    url = f"https://api.telegram.org/bot{_TOKEN}/sendDocument"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return "ok: файл отправлен"
    except Exception as e:
        return f"error: {e}"


def write_and_send_tg(filename: str, content: str, caption: str = "") -> str:
    """create temp file → send → delete"""
    tmp_dir = tempfile.mkdtemp()
    path    = os.path.join(tmp_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    result = send_file_to_tg(path, caption=caption or filename)
    os.remove(path)
    os.rmdir(tmp_dir)
    return result


TOOL_HANDLERS = {
    "send_file_to_tg":  lambda args: send_file_to_tg(**args),
    "write_and_send_tg": lambda args: write_and_send_tg(**args),
}