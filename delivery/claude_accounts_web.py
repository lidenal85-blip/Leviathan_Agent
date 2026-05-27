"""
delivery/claude_accounts_web.py
Веб-интерфейс для управления аккаунтами Claude.
Подключается к существующему FastAPI на порте 8200.

Архитектура:
- Использует AccountStore (шифрование через CryptoKeyManager)
- Проверяет аккаунт через ClaudeAdapter (тот же, что LLMProviderPool)
- Пароль ХРАНИТСЯ ТОЛЬКО зашифрованным (через Fernet)
- URL: /claude-accounts/
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from claude_manager.core.crypto.key_manager import get_crypto
from claude_manager.core.storage.account_store import Account, AccountStatus, AccountStore
from claude_manager.logger import StepLogger
from claude_manager.providers.claude.adapter import ClaudeAdapter, ClaudeAuthError

_log  = StepLogger("accounts_web")
_store   = AccountStore()
_adapter = ClaudeAdapter()

router = APIRouter(prefix="/claude-accounts", tags=["claude_accounts"])

# ── проверка аккаунта ─────────────────────────────────────────────────────

async def _login_and_get_session(email: str, password: str) -> dict:
    """
    Получает session_key с claude.ai через HTTP.
    Возвращает {ok, session_key, org_id, remaining, reset_ts, error}
    """
    _log.step(f"_login: попытка логина для {email}")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Шаг 1: логин
            resp = await client.post(
                "https://claude.ai/api/auth/signin",
                json={"email": email, "password": password},
                headers={"content-type": "application/json"},
            )
            if resp.status_code not in (200, 201):
                return {"ok": False, "error": f"HTTP {resp.status_code} при логине"}

            data = resp.json()
            session_key = data.get("sessionKey") or data.get("session_key", "")

            # если session_key не в теле — ищем в куки
            if not session_key:
                for c in resp.cookies.items():
                    if "session" in c[0].lower():
                        session_key = c[1]
                        break

            if not session_key:
                return {"ok": False, "error": "Не удалось получить session_key"}

            # Шаг 2: проверка через /api/organizations
            org_resp = await client.get(
                "https://claude.ai/api/organizations",
                headers={"cookie": f"sessionKey={session_key}"},
            )

            remaining = 100
            reset_ts  = 0.0
            org_id    = ""

            if org_resp.status_code == 200:
                orgs = org_resp.json()
                if orgs and isinstance(orgs, list):
                    org_id = orgs[0].get("uuid", "")
                remaining = int(org_resp.headers.get("x-ratelimit-remaining-requests", 100))
                reset_raw = org_resp.headers.get("x-ratelimit-reset", "")
                if reset_raw:
                    try:
                        reset_ts = float(reset_raw)
                    except ValueError:
                        import dateutil.parser
                        reset_ts = dateutil.parser.parse(reset_raw).timestamp()

            _log.step(f"_login: успех org_id={org_id} remaining={remaining}")
            return {
                "ok":          True,
                "session_key": session_key,
                "org_id":      org_id,
                "remaining":   remaining,
                "reset_ts":    reset_ts,
                "error":       "",
            }

    except httpx.TimeoutException:
        return {"ok": False, "error": "Таймаут подключения к claude.ai"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── HTML ────────────────────────────────────────────────────────────────────

def _render(
    accounts: list[dict],
    message: str = "",
    success: bool = False,
) -> str:
    """Рендерим HTML напрямую, без Jinja2 (не нужна папка templates)."""
    msg_html = ""
    if message:
        color = "#00ff88" if success else "#ff6b6b"
        msg_html = f'<p style="color:{color};font-weight:bold;margin:12px 0">{message}</p>'

    rows = ""
    for a in accounts:
        status_color = {
            "ACTIVE":       "#00ff88",
            "RATE_LIMITED": "#ffa500",
            "AUTH_FAILED":  "#ff4444",
            "DEAD":         "#888",
            "DEGRADED":     "#ffcc00",
        }.get(a["status"], "#ccc")

        reset_str = "—"
        if a.get("reset_ts") and a["reset_ts"] > time.time():
            wait = int(a["reset_ts"] - time.time())
            reset_str = f"через {wait//60}м {wait%60}с"

        rows += f"""
        <tr>
            <td>{a['email']}</td>
            <td style="color:{status_color}">{a['status']}</td>
            <td>{a.get('remaining', '?')}</td>
            <td>{reset_str}</td>
            <td>
                <form method="post" action="/claude-accounts/delete" style="display:inline">
                    <input type="hidden" name="account_id" value="{a['account_id']}">
                    <button type="submit" style="background:#c0392b;padding:4px 10px"
                            onclick="return confirm('Удалить {a['email']}?')">×</button>
                </form>
                <form method="post" action="/claude-accounts/recheck" style="display:inline">
                    <input type="hidden" name="account_id" value="{a['account_id']}">
                    <button type="submit" style="background:#2980b9;padding:4px 10px">↻</button>
                </form>
            </td>
        </tr>"""

    if not rows:
        rows = "<tr><td colspan=5 style='color:#666;text-align:center'>Аккаунтовнет</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Claude Account Manager — Leviathan</title>
    <meta http-equiv="refresh" content="60">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Segoe UI', sans-serif; background: #0d0d0d; color: #e0e0e0; padding: 30px; }}
        h1 {{ color: #00ff88; margin-bottom: 6px; font-size: 1.6em; }}
        h2 {{ color: #aaa; font-size: 1em; margin: 24px 0 10px; text-transform: uppercase; letter-spacing: 1px; }}
        .card {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 24px; max-width: 860px; margin: auto; }}
        .form-row {{ display: flex; gap: 10px; align-items: center; }}
        input[type=email], input[type=password] {{
            flex: 1; background: #111; border: 1px solid #333; color: #eee;
            padding: 10px 14px; border-radius: 6px; font-size: 0.95em;
        }}
        button[type=submit] {{
            background: #00a859; color: #fff; border: none; border-radius: 6px;
            padding: 10px 20px; cursor: pointer; font-size: 0.95em; white-space: nowrap;
        }}
        button[type=submit]:hover {{ background: #009048; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
        th {{ color: #666; font-size: 0.78em; text-transform: uppercase;
              letter-spacing: 0.5px; text-align: left; padding: 8px 10px;
              border-bottom: 1px solid #222; }}
        td {{ padding: 10px; border-bottom: 1px solid #1e1e1e; font-size: 0.9em; }}
        tr:hover td {{ background: #1f1f1f; }}
        .badge {{ display: inline-block; font-size: 0.7em; padding: 2px 8px;
                  border-radius: 12px; background: #222; }}
        .hint {{ color: #555; font-size: 0.78em; margin-top: 8px; }}
    </style>
</head>
<body>
<div class="card">
    <h1>🤖 Claude Account Manager</h1>
    <p class="hint">автообновление через 60с &nbsp;·&nbsp; пароли хранятся зашифрованными</p>

    <h2>➕ Добавить аккаунт</h2>
    <form method="post" action="/claude-accounts/add">
        <div class="form-row">
            <input type="email" name="email" placeholder="Email" required autocomplete="off">
            <input type="password" name="password" placeholder="Password" required autocomplete="off">
            <button type="submit">Проверить и добавить</button>
        </div>
    </form>
    {msg_html}

    <h2>📋 Аккаунты ({len(accounts)})</h2>
    <table>
        <tr>
            <th>Email</th><th>Статус</th><th>Остаток</th><th>Сброс</th><th>Действия</th>
        </tr>
        {rows}
    </table>
</div>
</body>
</html>"""


# ── вспомогатель: список аккаунтов для рендера ────────────────────────

async def _list_accounts() -> list[dict]:
    accounts = await _store.list_all()
    return [
        {
            "account_id": a.account_id,
            "email":      a.email,
            "status":     a.status.value if hasattr(a.status, 'value') else str(a.status),
            "remaining":  a.rate_limit_remaining,
            "reset_ts":   a.rate_limit_reset_ts,
        }
        for a in accounts
    ]


# ── роуты ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def accounts_page():
    """GET /claude-accounts/ — главная страница."""
    accounts = await _list_accounts()
    return HTMLResponse(_render(accounts))


@router.post("/add", response_class=HTMLResponse)
async def add_account(email: str = Form(...), password: str = Form(...)):
    """
    POST /claude-accounts/add
    1. Логин на claude.ai — получить session_key
    2. Шифруем пароль + session_key через CryptoKeyManager
    3. Сохраняем в AccountStore
    """
    _log.task(f"add_account: {email}")
    result = await _login_and_get_session(email, password)

    if result["ok"]:
        acc = Account(
            account_id=str(uuid.uuid4()),
            email=email,
            password=password,      # AccountStore сам зашифрует при save
            session_key=result["session_key"],
            status=AccountStatus.ACTIVE,
            rate_limit_remaining=result["remaining"],
            rate_limit_reset_ts=result["reset_ts"],
        )
        await _store.add_account(acc)
        msg = (
            f"✅ {email} добавлен. "
            f"Остаток: {result['remaining']} запросов."
        )
        _log.result(f"add_account: {email} добавлен, remaining={result['remaining']}")
        success = True
    else:
        # даже если логин не удался — сохраняем с AUTH_FAILED
        acc = Account(
            account_id=str(uuid.uuid4()),
            email=email,
            password=password,
            session_key="",
            status=AccountStatus.AUTH_FAILED,
        )
        await _store.add_account(acc)
        msg = f"⚠️ {email} сохранён, но ошибка проверки: {result['error']}"
        _log.warn(f"add_account: {email} ошибка: {result['error']}")
        success = False

    accounts = await _list_accounts()
    return HTMLResponse(_render(accounts, message=msg, success=success))


@router.post("/delete", response_class=HTMLResponse)
async def delete_account(account_id: str = Form(...)):
    """POST /claude-accounts/delete"""
    _log.task(f"delete_account: {account_id}")
    await _store.remove_account(account_id)
    _log.result(f"delete_account: {account_id} удалён")
    accounts = await _list_accounts()
    return HTMLResponse(_render(accounts, message="Аккаунт удалён", success=True))


@router.post("/recheck", response_class=HTMLResponse)
async def recheck_account(account_id: str = Form(...)):
    """
    POST /claude-accounts/recheck
    Перепроверяет аккаунт: GET /api/organizations
    Обновляет rate_limit_remaining + статус.
    """
    _log.task(f"recheck_account: {account_id}")
    try:
        acc = await _store.get_account(account_id)
        if not acc:
            return HTMLResponse(_render(
                await _list_accounts(),
                message="Аккаунт не найден",
                success=False,
            ))

        # проверяем через get_org_id адаптера
        try:
            org_id = await _adapter.get_org_id(acc.session_key)
            new_status = AccountStatus.ACTIVE
            msg = f"✅ {acc.email}: активен (org_id получен)"
            _log.result(f"recheck: {acc.email} ACTIVE")
        except ClaudeAuthError:
            new_status = AccountStatus.AUTH_FAILED
            msg = f"❌ {acc.email}: 401 — session_key устарел"
            _log.warn(f"recheck: {acc.email} AUTH_FAILED")

        await _store.update_status(account_id, new_status)
        success = new_status == AccountStatus.ACTIVE

    except Exception as e:
        msg = f"Ошибка: {e}"
        success = False
        _log.error(f"recheck: {e}")

    accounts = await _list_accounts()
    return HTMLResponse(_render(accounts, message=msg, success=success))