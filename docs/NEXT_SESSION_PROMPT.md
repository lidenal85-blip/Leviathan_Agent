# Передаточный промт — следующая сессия

Скопируй и отправь в начале новой сессии:

---

```
Репозиторий: github.com/lidenal85-blip/Leviathan_Agent
Сервер: root@78.17.24.96 | @Levi_Engi_bot

Прочитай docs/sessions/2026-05-25_deploy-and-arbitr.md

СТАТУС:
✅ Leviathan Agent v3.1 — порт 8200
✅ ArbitrCockpit v0.5  — порт 8095 (leviathanstory.ru/arbitr/)
✅ mark_dead + Groq fallback — пропатчено в репо, нужно накатить на сервер

ПЕРВЫЕ КОМАНДЫ:
ssh levi "cp /tmp/core_backup.py /opt/leviathan_engine/agent_service/agent/core.py"
ssh levi "cd /opt/leviathan_engine/agent_service && git pull --ff-only && \
  venv/bin/pip install groq -q && \
  systemctl restart leviathan_agent && sleep 3 && \
  curl http://localhost:8200/health"

ЕСЛИ git pull конфликт:
ssh levi "cd /opt/leviathan_engine/agent_service && \
  git fetch origin && git reset --hard origin/main && \
  venv/bin/pip install groq -q && \
  systemctl restart leviathan_agent && sleep 3 && \
  curl http://localhost:8200/health"

ЗАДАЧИ (приоритет):
1. 🔴 Накатить патч (команды выше) — агент сейчас DOWN
2. 🔴 Тест Groq fallback — заблокировать ключ и отправить задачу
3. 🟡 Тест ArbitrCockpit pipeline через агента
4. 🟡 Понять причину 403 на Gemini ключах
5. 🟢 Удалить /root/Leviathan_Agent/
```

---

## Инфраструктура

| Сервис | Порт | Путь | Статус |
|---|---|---|---|
| Leviathan Agent | 8200 | /opt/leviathan_engine/agent_service/ | ⚠️ DOWN (SyntaxError — патч в репо) |
| ArbitrCockpit | 8095 | /opt/arbitr_cockpit/ | ✅ |
| nginx → Arbitr | — | leviathanstory.ru/arbitr/ | ✅ |

## Что в core.py изменилось

```python
# 1. 403 handler (новый) — пропускает мёртвые ключи
if "403" in err_str or "API_KEY_INVALID" in err_str:
    self.key_pool.mark_dead(key)   # блок на 24ч
    continue                        # пробует следующий ключ

# 2. Groq fallback (новый) — когда ВСЕ Gemini ключи мертвы
groq_result = await self._groq_fallback(task, messages)
# Использует GROQ_K1..K5 из .env, модель llama-3.3-70b-versatile

# 3. mark_dead() в key_pool.py — блок на 86400 секунд (24ч)
```
