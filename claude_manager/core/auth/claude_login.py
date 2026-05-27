"""
ClaudeLogin — автоматическое получение sessionKey через Playwright.

Схема:
  1. Запуск видимого/безголовного Chromium
  2. Открываем claude.ai/login
  3. Вводим email, клик «Продолжить»
  4. Google/Штатный вход — если переданы credentials
  5. Ожидаем появления cookie sessionKey
  6. Возвращаем значение

Интеграция с AccountLifecycleManager:
  - _do_rotate() → ClaudeLogin.get_session_key(email, password)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_LOGIN_URL = "https://claude.ai/login"
CLAUDE_ORIGIN    = "https://claude.ai"
SESSION_COOKIE   = "sessionKey"

# Сколько ждём появления cookie после отправки формы, секунд
_WAIT_COOKIE_S   = 30
# Timeout на всю операцию
_OP_TIMEOUT_S    = 90


@dataclass
class LoginResult:
    success: bool
    session_key: Optional[str] = None
    error: Optional[str]       = None


@dataclass
class ClaudeLoginConfig:
    headless: bool  = True
    slow_mo:  int   = 80           # ms между действиями (мимикует человека)
    viewport: dict  = field(default_factory=lambda: {"width": 1280, "height": 800})
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    # если True — сохраняем скриншот при ошибке
    debug_screenshot: bool = True
    screenshot_path:  str  = "/tmp/claude_login_debug.png"


class ClaudeLogin:
    """Получает sessionKey через Playwright Chromium."""

    def __init__(self, config: Optional[ClaudeLoginConfig] = None):
        self.cfg = config or ClaudeLoginConfig()

    async def get_session_key(
        self,
        email: str,
        password: str,
        google_account: bool = False,
    ) -> LoginResult:
        """
        Главный метод. Возвращает LoginResult.

        google_account=True — если аккаунт привязан к Google.
        """
        try:
            return await asyncio.wait_for(
                self._login_flow(email, password, google_account),
                timeout=_OP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return LoginResult(False, error=f"Timeout {_OP_TIMEOUT_S}s")
        except Exception as exc:
            logger.exception("ClaudeLogin: неожиданная ошибка")
            return LoginResult(False, error=str(exc))

    # ──────────────────────────────────────────────────────────────────────────────

    async def _login_flow(
        self,
        email: str,
        password: str,
        google_account: bool,
    ) -> LoginResult:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.cfg.headless,
                slow_mo=self.cfg.slow_mo,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                viewport=self.cfg.viewport,
                user_agent=self.cfg.user_agent,
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Скрываем признаки automation
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = await context.new_page()

            try:
                result = await self._do_login(
                    page, context, email, password, google_account
                )
            except Exception as exc:
                if self.cfg.debug_screenshot:
                    try:
                        await page.screenshot(path=self.cfg.screenshot_path)
                        logger.info(
                            "ClaudeLogin: debug screenshot → %s",
                            self.cfg.screenshot_path,
                        )
                    except Exception:
                        pass
                raise
            finally:
                await browser.close()

        return result

    async def _do_login(
        self,
        page,
        context,
        email: str,
        password: str,
        google_account: bool,
    ) -> LoginResult:
        logger.info("ClaudeLogin: открываю %s", CLAUDE_LOGIN_URL)
        await page.goto(CLAUDE_LOGIN_URL, wait_until="networkidle")

        # Шаг 1: ввод email
        email_sel = 'input[type="email"], input[name="email"], input[placeholder*="mail"]'
        await page.wait_for_selector(email_sel, timeout=15_000)
        await page.fill(email_sel, email)
        logger.info("ClaudeLogin: email введён")

        # Целим click на Continue / Далее
        continue_sel = 'button[type="submit"], button:has-text("Continue"), button:has-text("Далее")'
        await page.click(continue_sel)
        await page.wait_for_timeout(1500)

        # Шаг 2: Google-аутентификация или email+password
        if google_account:
            result = await self._google_flow(page, email, password)
        else:
            result = await self._password_flow(page, password)

        if not result.success:
            return result

        # Шаг 3: ждём sessionKey в cookies
        session_key = await self._wait_for_session_key(context)
        if session_key:
            logger.info("ClaudeLogin: sessionKey получен (длина %d)", len(session_key))
            return LoginResult(True, session_key=session_key)

        return LoginResult(False, error="sessionKey не появился в cookies")

    async def _password_flow(self, page, password: str) -> LoginResult:
        """Email+password вход."""
        pw_sel = 'input[type="password"]'
        try:
            await page.wait_for_selector(pw_sel, timeout=10_000)
        except Exception:
            return LoginResult(False, error="Поле password не появилось")
        await page.fill(pw_sel, password)
        await page.click('button[type="submit"]')
        logger.info("ClaudeLogin: password отправлен")
        return LoginResult(True)

    async def _google_flow(self, page, email: str, password: str) -> LoginResult:
        """Google OAuth вход."""
        # Нажимаем кнопку «Sign in with Google»
        google_btn = 'button:has-text("Google"), a:has-text("Google"), [data-provider="google"]'
        try:
            await page.click(google_btn, timeout=8_000)
        except Exception:
            return LoginResult(False, error="Кнопка Google не найдена")

        # Google открывается в новой вкладке — переключаемся
        google_page = await page.context.wait_for_event("page", timeout=10_000)
        await google_page.wait_for_load_state("networkidle")

        # Вводим email Google
        g_email_sel = 'input[type="email"]'
        await google_page.wait_for_selector(g_email_sel, timeout=10_000)
        await google_page.fill(g_email_sel, email)
        await google_page.click('#identifierNext button, [jsname="LgbsSe"]')
        await google_page.wait_for_timeout(2000)

        # Вводим password
        g_pw_sel = 'input[type="password"]'
        await google_page.wait_for_selector(g_pw_sel, timeout=10_000)
        await google_page.fill(g_pw_sel, password)
        await google_page.click('#passwordNext button, [jsname="LgbsSe"]')
        logger.info("ClaudeLogin: Google credentials отправлены")
        return LoginResult(True)

    async def _wait_for_session_key(self, context) -> Optional[str]:
        """Poll cookies пока не появится sessionKey."""
        for _ in range(_WAIT_COOKIE_S * 2):   # проверяем каждые 0.5с
            cookies = await context.cookies(CLAUDE_ORIGIN)
            for c in cookies:
                if c["name"] == SESSION_COOKIE and c.get("value"):
                    return c["value"]
            await asyncio.sleep(0.5)
        return None