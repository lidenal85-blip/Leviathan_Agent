"""
agent/tools_deploy.py — инструменты деплоя LEVIATHAN AGENT

Инструменты:
  - deploy_service   : создать systemd сервис + запустить
  - nginx_add_location: добавить location в nginx конфиг левиафана
  - create_project   : создать структуру проекта (venv, папки, .env)
  - agent_log        : записать событие в лог агента
"""
from __future__ import annotations

import asyncio
import logging
import os
import json
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_LOG_FILE = os.environ.get("AGENT_LOG_FILE", "/opt/leviathan_engine/agent_service/data/agent_events.jsonl")
NGINX_CONF     = os.environ.get("NGINX_CONF", "/etc/nginx/sites-enabled/leviathanstory")


# ══════════════════════════════════════════════════════════════
# ВНУТРЕННИЕ ХЕЛПЕРЫ
# ══════════════════════════════════════════════════════════════

async def _bash(cmd: str, workdir: str = "/root") -> dict:
    """Внутренний bash-runner."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir if os.path.exists(workdir) else "/root",
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout.decode(errors="replace")[:4000],
            "stderr": stderr.decode(errors="replace")[:2000],
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Таймаут 120с"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
# AGENT LOG
# ══════════════════════════════════════════════════════════════

async def agent_log(
    project_id: str,
    stage: str,
    action: str,
    result: str,
    level: str = "INFO",
) -> dict:
    """
    Записывает событие агента в structured лог.
    Уровни: INFO, SUCCESS, WARNING, ERROR.
    """
    event = {
        "ts": datetime.utcnow().isoformat(),
        "project_id": project_id,
        "stage": stage,
        "action": action,
        "result": result[:500],
        "level": level.upper(),
    }
    try:
        log_path = Path(AGENT_LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        logger.info("agent_log: [%s] %s / %s", level, project_id, action)
        return {"ok": True, "event": event}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
# CREATE PROJECT
# ══════════════════════════════════════════════════════════════

async def create_project(
    name: str,
    base_dir: str = "/opt",
    project_type: str = "fastapi",
    port: int = 0,
) -> dict:
    """
    Создаёт структуру проекта:
      - Папки: app/, static/, data/, logs/, tests/
      - Python venv
      - .env с PORT
      - requirements.txt (базовый для типа)
      - README.md

    project_type: fastapi | telegram_bot | script
    port: если 0 — найдёт свободный от 8300
    """
    project_path = Path(base_dir) / name

    # Найти свободный порт если не указан
    if port == 0 and project_type == "fastapi":
        result = await _bash("ss -tlnp | grep LISTEN | awk '{print $4}' | grep -oP ':\\K\\d+' | sort -n")
        used_ports = set(int(p) for p in result.get("stdout", "").split() if p.isdigit())
        port = next(p for p in range(8300, 8400) if p not in used_ports)

    # Структура папок
    dirs = ["app", "data", "logs", "tests"]
    if project_type == "fastapi":
        dirs += ["app/routers", "app/models", "app/static", "app/templates"]
    elif project_type == "telegram_bot":
        dirs += ["app/handlers", "app/keyboards", "app/middlewares"]

    for d in dirs:
        await _bash(f"mkdir -p {project_path / d}")

    # requirements.txt
    reqs = {
        "fastapi": "fastapi\nuvicorn[standard]\npython-dotenv\nsqlalchemy\naiosqlite\nhttpx\n",
        "telegram_bot": "aiogram==3.*\npython-dotenv\nhttpx\naiosqlite\n",
        "script": "python-dotenv\nhttpx\n",
    }
    req_content = reqs.get(project_type, reqs["script"])
    (project_path / "requirements.txt").write_text(req_content)

    # .env
    env_content = f"PORT={port}\nAPP_NAME={name}\nENV=prod\nLOG_LEVEL=INFO\n"
    (project_path / ".env").write_text(env_content)

    # README
    (project_path / "README.md").write_text(f"# {name}\n\nСоздан агентом LEVIATHAN {datetime.utcnow().date()}\n\nТип: {project_type}\n")

    # venv
    venv_result = await _bash(f"python3 -m venv {project_path}/venv", workdir=str(project_path))

    # pip install
    pip_result = await _bash(
        f"{project_path}/venv/bin/pip install -r requirements.txt --quiet",
        workdir=str(project_path)
    )

    return {
        "ok": pip_result.get("ok", False),
        "project_path": str(project_path),
        "port": port,
        "project_type": project_type,
        "venv": str(project_path / "venv"),
        "dirs_created": dirs,
        "pip_output": pip_result.get("stdout", "")[:500],
    }


# ══════════════════════════════════════════════════════════════
# DEPLOY SERVICE
# ══════════════════════════════════════════════════════════════

async def deploy_service(
    name: str,
    project_path: str,
    entry_point: str,
    port: int,
    description: str = "",
    env_file: str = "",
) -> dict:
    """
    Создаёт systemd unit и запускает сервис.

    name         : имя сервиса (без .service)
    project_path : абсолютный путь к проекту
    entry_point  : команда запуска относительно venv/bin/
                   Примеры:
                     "uvicorn app.main:app --host 127.0.0.1 --port {port} --workers 1"
                     "python bot.py"
    port         : порт сервиса
    env_file     : путь к .env (если пусто — {project_path}/.env)
    """
    p = Path(project_path)
    venv_bin = p / "venv" / "bin"
    env_path = env_file or str(p / ".env")

    # Собираем ExecStart
    if entry_point.startswith("uvicorn"):
        exec_start = f"{venv_bin}/uvicorn {entry_point.split('uvicorn', 1)[1].strip()}"
    elif entry_point.startswith("python"):
        exec_start = f"{venv_bin}/python {entry_point.split('python', 1)[1].strip()}"
    else:
        exec_start = f"{venv_bin}/{entry_point}"

    service_content = f"""[Unit]
Description={description or name}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={project_path}
EnvironmentFile={env_path}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    service_path = f"/etc/systemd/system/{name}.service"
    Path(service_path).write_text(service_content)
    logger.info("deploy_service: написан %s", service_path)

    # Запуск
    cmds = [
        "systemctl daemon-reload",
        f"systemctl enable {name}",
        f"systemctl restart {name}",
    ]
    results = []
    for cmd in cmds:
        r = await _bash(cmd)
        results.append({"cmd": cmd, "ok": r["ok"], "stderr": r.get("stderr", "")[:200]})
        if not r["ok"]:
            return {
                "ok": False,
                "failed_at": cmd,
                "results": results,
                "service_path": service_path,
            }

    # Проверка что поднялся
    await asyncio.sleep(4)
    status = await _bash(f"systemctl is-active {name}")
    active = status.get("stdout", "").strip() == "active"

    # Проверка порта
    port_check = await _bash(f"ss -tlnp | grep :{port}")
    port_ok = port_check.get("ok") and str(port) in port_check.get("stdout", "")

    return {
        "ok": active,
        "active": active,
        "port_listening": port_ok,
        "service": f"{name}.service",
        "service_path": service_path,
        "port": port,
        "steps": results,
    }


# ══════════════════════════════════════════════════════════════
# NGINX ADD LOCATION
# ══════════════════════════════════════════════════════════════

async def nginx_add_location(
    location_path: str,
    upstream_port: int,
    websocket: bool = False,
    static_dir: str = "",
) -> dict:
    """
    Добавляет location блок в leviathanstory nginx конфиг.

    location_path  : URL путь, например "/myapp/"
    upstream_port  : внутренний порт приложения
    websocket      : добавить Upgrade/Connection заголовки
    static_dir     : если указан — добавит location для статики

    ВАЖНО: добавляет ПЕРЕД последним "location / {" чтобы не перекрыть главный маршрут.
    """
    nginx_path = Path(NGINX_CONF)
    if not nginx_path.exists():
        return {"ok": False, "error": f"nginx конфиг не найден: {NGINX_CONF}"}

    original = nginx_path.read_text()

    # Проверить что такой location уже не добавлен
    if f"location {location_path}" in original:
        return {"ok": True, "skipped": True, "reason": f"location {location_path} уже существует"}

    ws_headers = """
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";""" if websocket else ""

    location_block = f"""
    location {location_path} {{
        proxy_pass http://127.0.0.1:{upstream_port}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;{ws_headers}
        proxy_read_timeout 120s;
        client_max_body_size 50M;
    }}
"""

    if static_dir:
        static_path = location_path.rstrip("/") + "/static/"
        location_block += f"""
    location {static_path} {{
        alias {static_dir}/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }}
"""

    # Вставить перед "location / {"
    if "location / {" in original:
        new_conf = original.replace("    location / {", location_block + "    location / {", 1)
    else:
        # Вставить перед последней закрывающей скобкой server блока
        new_conf = original.rstrip() + "\n" + location_block + "\n}\n"

    # Записать
    # Бэкап
    backup = str(nginx_path) + ".bak"
    Path(backup).write_text(original)
    nginx_path.write_text(new_conf)

    # Тест конфига
    test = await _bash("nginx -t")
    if not test["ok"]:
        # Откат
        nginx_path.write_text(original)
        return {
            "ok": False,
            "error": "nginx -t провалился, откат",
            "nginx_error": test.get("stderr", ""),
        }

    # Reload
    reload_r = await _bash("systemctl reload nginx")

    return {
        "ok": reload_r["ok"],
        "location": location_path,
        "upstream_port": upstream_port,
        "nginx_conf": NGINX_CONF,
        "backup": backup,
    }


# ══════════════════════════════════════════════════════════════
# РЕЕСТР
# ══════════════════════════════════════════════════════════════

DEPLOY_TOOLS_REGISTRY = {
    "create_project":    create_project,
    "deploy_service":    deploy_service,
    "nginx_add_location": nginx_add_location,
    "agent_log":         agent_log,
}

DEPLOY_GEMINI_TOOLS = [
    {
        "name": "create_project",
        "description": (
            "Создаёт структуру нового проекта: папки, venv, requirements.txt, .env. "
            "Используй перед написанием кода нового приложения. "
            "Типы: fastapi, telegram_bot, script."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name":         {"type": "string",  "description": "Имя проекта (папка будет /opt/name)"},
                "base_dir":     {"type": "string",  "description": "Базовая директория (по умолчанию /opt)"},
                "project_type": {"type": "string",  "description": "Тип: fastapi | telegram_bot | script"},
                "port":         {"type": "integer", "description": "Порт (0 = найти свободный автоматически)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "deploy_service",
        "description": (
            "Создаёт systemd сервис и запускает приложение. "
            "Используй после того как код написан и готов к запуску. "
            "Проверяет что сервис активен и порт слушается."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name":         {"type": "string",  "description": "Имя сервиса (например: myapp)"},
                "project_path": {"type": "string",  "description": "Абсолютный путь к проекту"},
                "entry_point":  {"type": "string",  "description": "Команда запуска, например: uvicorn app.main:app --host 127.0.0.1 --port 8301 --workers 1"},
                "port":         {"type": "integer", "description": "Порт приложения"},
                "description":  {"type": "string",  "description": "Описание сервиса для systemd"},
                "env_file":     {"type": "string",  "description": "Путь к .env файлу"},
            },
            "required": ["name", "project_path", "entry_point", "port"],
        },
    },
    {
        "name": "nginx_add_location",
        "description": (
            "Добавляет location блок в nginx конфиг leviathanstory.ru. "
            "Используй после деплоя сервиса чтобы сделать его доступным через leviathanstory.ru/путь/. "
            "Автоматически делает backup и откатывает если nginx -t провалился."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location_path":  {"type": "string",  "description": "URL путь, например /myapp/"},
                "upstream_port":  {"type": "integer", "description": "Внутренний порт приложения"},
                "websocket":      {"type": "boolean", "description": "Добавить WebSocket заголовки"},
                "static_dir":     {"type": "string",  "description": "Путь к папке статики (опционально)"},
            },
            "required": ["location_path", "upstream_port"],
        },
    },
    {
        "name": "agent_log",
        "description": (
            "Записывает событие в structured лог агента. "
            "Используй в начале и конце каждого этапа работы над проектом. "
            "Уровни: INFO, SUCCESS, WARNING, ERROR."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ID или имя проекта"},
                "stage":      {"type": "string", "description": "Этап: setup | coding | testing | deploy | done"},
                "action":     {"type": "string", "description": "Что сделано"},
                "result":     {"type": "string", "description": "Результат или ошибка"},
                "level":      {"type": "string", "description": "INFO | SUCCESS | WARNING | ERROR"},
            },
            "required": ["project_id", "stage", "action", "result"],
        },
    },
]


def register_deploy_tools(tools_registry: dict, gemini_tools: list) -> None:
    """Регистрирует deploy инструменты в реестре агента."""
    tools_registry.update(DEPLOY_TOOLS_REGISTRY)
    gemini_tools.extend(DEPLOY_GEMINI_TOOLS)
    logger.info("Deploy tools зарегистрированы: %d инструментов", len(DEPLOY_TOOLS_REGISTRY))
