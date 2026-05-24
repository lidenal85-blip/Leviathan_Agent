#!/usr/bin/env bash
# ═══════════════════════════════════════════════
# deploy.sh — установка LEVIATHAN AGENT на сервер
# Запуск: bash deploy.sh
# ═══════════════════════════════════════════════
set -e

REPO="https://github.com/lidenal85-blip/Leviathan_Agent"
INSTALL_DIR="/opt/leviathan_agent"
SERVICE="leviathan_agent"
PORT=8200

G='\033[32m'; R='\033[31m'; C='\033[36m'; X='\033[0m'
ok()   { echo -e "${G}[OK]${X} $*"; }
info() { echo -e "${C}[>>]${X} $*"; }
die()  { echo -e "${R}[!!]${X} $*"; exit 1; }

echo "════════════════════════════════════"
echo " LEVIATHAN AGENT — DEPLOY"
echo "════════════════════════════════════"

# Клонируем или обновляем
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Обновляем репозиторий..."
    cd "$INSTALL_DIR" && git pull origin main
else
    info "Клонируем репозиторий..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# venv
info "Создаём venv..."
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -r requirements.txt
ok "Зависимости установлены"

# .env
if [ ! -f ".env" ]; then
    info "Создаём .env из примера..."
    cp .env.example .env
    echo ""
    echo "⚠️  Заполни /opt/leviathan_agent/.env:"
    echo "    GEMINI_KEYS=ключ1,ключ2"
    echo "    TG_BOT_TOKEN=токен"
    echo "    TG_ADMIN_CHAT_ID=chat_id"
    echo ""
fi

# nginx
info "Настраиваем nginx..."
cat > /etc/nginx/sites-enabled/leviathan_agent.conf << 'EOF'
# Добавить в основной server блок leviathanstory.ru:
# location /agent/ {
#     proxy_pass http://127.0.0.1:8200/;
#     proxy_set_header Host $host;
#     proxy_set_header X-Real-IP $remote_addr;
# }
# location /agent/ws {
#     proxy_pass http://127.0.0.1:8200/ws;
#     proxy_http_version 1.1;
#     proxy_set_header Upgrade $http_upgrade;
#     proxy_set_header Connection "upgrade";
# }
EOF

# systemd
info "Устанавливаем systemd сервис..."
cp leviathan_agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable leviathan_agent
systemctl restart leviathan_agent

sleep 2
if systemctl is-active --quiet leviathan_agent; then
    ok "Сервис запущен!"
else
    die "Сервис не запустился. Смотри: journalctl -u leviathan_agent -n 30"
fi

echo ""
echo "════════════════════════════════════"
ok "LEVIATHAN AGENT развёрнут на порту $PORT"
echo "    Dashboard: http://localhost:$PORT"
echo "    Health:    http://localhost:$PORT/health"
echo "    Добавь /agent/ в nginx конфиг"
echo "════════════════════════════════════"
