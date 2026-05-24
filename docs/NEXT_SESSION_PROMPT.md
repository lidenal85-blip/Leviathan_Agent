# Передаточный промт — следующая сессия

Скопируй и отправь в начале новой сессии:

---

```
Репозиторий: github.com/lidenal85-blip/Leviathan_Agent
Сервер: root@78.17.24.96 | leviathanstory.ru
Telegram: @Levi_Engi_bot (работает)

Прочитай docs/sessions/2026-05-25_deploy-and-arbitr.md — последний отчёт.

СТАТУС:
- Leviathan Agent v3.1 задеплоен, работает на порту 8200 ✅
- Telegram бот активен ✅
- ArbitrCockpit интеграция — код готов, нужно тестировать

ЗАДАЧА СЕССИИ: ArbitrCockpit интеграция

Шаг 1 — проверить что Arbitr запущен:
  curl http://localhost:8090/health

Шаг 2 — если не запущен, найти где он живёт:
  find /opt /root /var/www -name "*.py" | xargs grep -l "arbitr" 2>/dev/null | head -5

Шаг 3 — протестировать arbitr инструменты через агента:
  POST http://localhost:8200/api/tasks
  {"prompt": "Оцени проект: Telegram бот для записи клиентов с оплатой ЮКасса. Используй arbitr_lisa_estimate с project_type=bot_fsm.", "mode": "NORMAL"}

Шаг 4 — если Arbitr живой, тест полного пайплайна:
  {"prompt": "Найди первый заказ в ArbitrCockpit и покажи его статус конвейера", "mode": "NORMAL"}

Шаг 5 — задокументировать результат, обновить SYSTEM_PROMPT если нужно.
```

---

## Архитектура (быстрый контекст)

```
Leviathan Agent (8200)
  └── agent/tools_arbitr.py → ArbitrCockpit API (8090)
        ├── arbitr_lisa_estimate     (автономно, без сети)
        ├── arbitr_pipeline_status   → GET /api/orders/{id}/pipeline
        ├── arbitr_pipeline_start    → POST /api/orders/{id}/pipeline/advance
        ├── arbitr_render_prompt     → GET /api/orders/{id}/pipeline/{run_id}
        ├── arbitr_submit_response   → POST /api/orders/{id}/pipeline/{run_id}/submit-response
        └── arbitr_run_auto_stage    → POST /api/orders/{id}/pipeline/{run_id}/run-auto
```

## Порты на сервере
```
8200 — Leviathan Agent  ✅ работает
8090 — ArbitrCockpit    ❓ проверить
8005 — Orionyx
8000 — AI Outreach
8120 — VoiceStudio
8110 — KinoVibe
```

## Если ArbitrCockpit не запущен
```bash
# Найти и запустить
find /opt -name "main.py" | xargs grep -l "arbitr\|cockpit" 2>/dev/null
cd /opt/arbitr_cockpit  # или где он живёт
source venv/bin/activate && uvicorn app.main:app --port 8090 &
```
