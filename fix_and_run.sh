#!/bin/bash

echo "🔧 Leviathan Agent — Fix & Run"
echo "==============================="
cd /opt/leviathan_engine/agent_service || exit 1

# 1. Создаём недостающие __init__.py
echo "📁 Creating __init__.py files..."
touch delivery/__init__.py
touch claude_manager/__init__.py
touch claude_manager/core/__init__.py
touch claude_manager/domain/__init__.py
touch claude_manager/providers/__init__.py

# 2. Проверяем синтаксис
echo "🔍 Checking syntax..."
python3 -m py_compile delivery/claude_accounts_web.py 2>/dev/null && echo "   ✅ claude_accounts_web.py OK" || echo "   ❌ claude_accounts_web.py has errors"

# 3. Проверяем импорт
echo "📦 Testing import..."
python3 -c "from delivery.claude_accounts_web import router; print('   ✅ Import OK')" 2>&1

# 4. Добавляем роутер в main.py, если ещё нет
if ! grep -q "claude_accounts_web" main.py; then
    echo "➕ Adding router to main.py..."
    # Находим строку с app = FastAPI( и добавляем после неё
    sed -i '/app = FastAPI(/a \
\
# Подключение веб-интерфейса для аккаунтов Claude\
try:\
    from delivery.claude_accounts_web import router as claude_accounts_router\
    app.include_router(claude_accounts_router)\
    print("✅ Claude accounts web interface loaded")\
except Exception as e:\
    print(f"⚠️ Claude accounts web interface not loaded: {e}")' main.py
    echo "   ✅ Router added"
else
    echo "   ✅ Router already present"
fi

# 5. Устанавливаем jinja2 если нет
echo "📦 Installing jinja2..."
pip3 install jinja2 -q

# 6. Проверяем базу данных
echo "🗄️ Checking database..."
sqlite3 claude_accounts.db "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, email TEXT, password TEXT, session_key TEXT, status TEXT, remaining_requests TEXT, reset_time TEXT, last_check TIMESTAMP);" 2>/dev/null
echo "   ✅ Database ready"

# 7. Запускаем агента
echo ""
echo "🚀 Starting Leviathan Agent..."
echo "   Press Ctrl+C to stop"
echo "==============================="
python3 main.py
