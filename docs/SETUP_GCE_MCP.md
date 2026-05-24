# Настройка Google Compute Engine MCP

## Шаг 1: GCP Console

1. Зайди: https://console.cloud.google.com
2. Выбери (или создай) проект — например `leviathan-ecosystem`
3. Включи API: **Compute Engine API**
   - Поиск → "Compute Engine API" → Enable

## Шаг 2: Service Account

```
IAM & Admin → Service Accounts → + CREATE SERVICE ACCOUNT

Name:  leviathan-agent
ID:    leviathan-agent
```

Роли (добавить все три):
- `Compute Instance Admin (v1)` — управление VM
- `Compute Viewer` — чтение состояния
- `Service Account User` — от имени SA

**Create and continue → Done**

## Шаг 3: JSON ключ

```
Service Accounts → leviathan-agent → Keys → ADD KEY → Create new key → JSON
```

Скачается файл `leviathan-ecosystem-XXXX.json`

## Шаг 4: Добавить ключ в .env агента

```bash
# На сервере:
cp /path/to/leviathan-ecosystem-XXXX.json /opt/leviathan_engine/agent_service/gcp_key.json
chmod 600 /opt/leviathan_engine/agent_service/gcp_key.json

# В .env добавить:
GCP_KEY_PATH=/opt/leviathan_engine/agent_service/gcp_key.json
GCP_PROJECT_ID=leviathan-ecosystem
GCP_ZONE=europe-west3-a   # или ближайший к серверу
GCP_INSTANCE_NAME=leviathan-main  # имя твоей VM
```

## Шаг 5: Добавить GCE tools в агента

```bash
# agent/tools_gce.py (создать в следующей сессии)
# Инструменты:
#   gce_instance_status  — статус VM (RUNNING/STOPPED)
#   gce_instance_start   — запустить VM
#   gce_instance_stop    — остановить VM (требует FULL режим)
#   gce_ssh_command      — выполнить команду через gcloud compute ssh
```

## Альтернатива без GCE MCP

Агент уже умеет управлять сервером через bash_tool.
GCE MCP нужен только если хочешь управлять самой VM (перезапуск, масштабирование).

Для текущих задач (деплой кода, перезапуск сервисов) достаточно текущего агента.
