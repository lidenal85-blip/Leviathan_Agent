# LEVIATHAN AGENT — Production Deploy Guide

## Быстрый старт

```bash
ssh root@78.17.24.96
git clone https://github.com/lidenal85-blip/Leviathan_Agent /opt/leviathan_agent
cd /opt/leviathan_agent
bash deploy.sh
```

## .env на сервере

```bash
cat > /opt/leviathan_agent/.env << 'ENV'
# Gemini ключи — взять из /opt/leviathan_engine/
GEMINI_KEYS=key1,key2,key3,key4,key5,key6,key7

# Telegram
TG_BOT_TOKEN=8604646197:AAFJM_c38cGBxICgUDWB-jEtgImUz0Fyo2M
TG_ADMIN_CHAT_ID=7709651193

# GitHub
GITHUB_TOKEN=ghp_your_token_here
GITHUB_USERNAME=lidenal85-blip

# Anthropic (Claude fallback)
ANTHROPIC_API_KEY=sk-ant-your-key

# Сервер
HOST=0.0.0.0
PORT=8200
SECRET_KEY=leviathan_prod_secret_2026

# Пути
WORKSPACE=/var/www
LEVIATHAN_ENGINE=/opt/leviathan_engine
ARBITR_URL=http://localhost:8090

# Режим
DEFAULT_MODE=NORMAL
MODEL_MODE=AUTO
ENV
```

## nginx — добавить в /etc/nginx/sites-enabled/leviathanstory

```nginx
# Добавить внутрь server { listen 443 ssl; server_name leviathanstory.ru; }

location /agent/ws {
    proxy_pass http://127.0.0.1:8200/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}

location /agent/ {
    proxy_pass http://127.0.0.1:8200/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 300s;
}
```

После добавления:
```bash
nginx -t && systemctl reload nginx
```

## MCP для Cursor IDE

Создай `/root/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "leviathan": {
      "command": "python3",
      "args": ["/opt/leviathan_agent/mcp_server/leviathan_mcp.py"]
    }
  }
}
```

## Проверка

```bash
# Статус сервиса
systemctl status leviathan_agent

# Health check
curl -s http://localhost:8200/health | python3 -m json.tool

# Тест через nginx
curl -s https://leviathanstory.ru/agent/health

# Дашборд
open https://leviathanstory.ru/agent/
```

## Получить Gemini ключи с LEVIATHAN Engine

```bash
grep -r "GEMINI\|gemini" /opt/leviathan_engine/.env 2>/dev/null | head -20
cat /opt/leviathan_engine/config.py 2>/dev/null | grep -i gemini | head -10
```
