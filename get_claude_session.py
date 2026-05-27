#!/usr/bin/env python3
"""
Получение sessionKey Claude через Playwright (реальный браузер)
Сохраняет sessionKey в базу claude_accounts.db
"""

import asyncio
import sqlite3
from playwright.async_api import async_playwright

async def get_session_key(email: str, password: str) -> str | None:
    """Логинится в Claude через браузер и возвращает sessionKey"""
    
    async with async_playwright() as p:
        # Запускаем браузер (headless=False, чтобы видеть и помочь с капчей)
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        print(f"🌐 Открываю claude.ai для {email}")
        await page.goto("https://claude.ai")
        
        # Ждём форму логина
        await page.wait_for_selector("input[type='email']", timeout=30000)
        
        # Вводим email
        await page.fill("input[type='email']", email)
        await page.click("button[type='submit']")
        
        # Ждём поле пароля
        await page.wait_for_selector("input[type='password']", timeout=10000)
        
        # Вводим пароль
        await page.fill("input[type='password']", password)
        await page.click("button[type='submit']")
        
        # Ждём загрузки после логина
        await page.wait_for_url("https://claude.ai/chats", timeout=30000)
        
        # Получаем cookies
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie['name'] == 'sessionKey':
                session_key = cookie['value']
                print(f"✅ Получен sessionKey для {email}")
                return session_key
        
        print(f"❌ sessionKey не найден для {email}")
        return None

def save_to_db(email: str, session_key: str):
    """Сохраняет sessionKey в базу"""
    conn = sqlite3.connect("claude_accounts.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            session_key TEXT,
            status TEXT,
            last_check TIMESTAMP
        )
    """)
    
    cursor.execute("""
        INSERT OR REPLACE INTO accounts (id, email, session_key, status, last_check)
        VALUES (?, ?, ?, ?, ?)
    """, (
        email.replace("@", "_").replace(".", "_"),
        email,
        session_key,
        "active",
        "2026-05-26 12:30:00"
    ))
    
    conn.commit()
    conn.close()
    print(f"💾 {email} сохранён в базу")

async def main():
    # Твои аккаунты
    accounts = [
        ("delial19850414@gmail.com", "dEN4IK1985!"),
        ("aliden198504@gmail.com", "DeN4IK1985!"),
        ("1805tatar@gmail.com", "1805/Taty"),
    ]
    
    for email, password in accounts:
        print(f"\n🔐 Обработка {email}")
        session_key = await get_session_key(email, password)
        if session_key:
            save_to_db(email, session_key)
        else:
            print(f"   ❌ Не удалось получить sessionKey для {email}")
        
        # Пауза между аккаунтами
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
