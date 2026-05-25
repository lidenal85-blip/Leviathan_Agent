# Передаточный промт — следующая сессия

Скопируй и отправь в начале новой сессии:

---

```
Репо: github.com/lidenal85-blip/Leviathan_Agent (f4704f9)
      github.com/lidenal85-blip/ArbitrCockpit (37c0a74)
Сервер: root@78.17.24.96 | @Levi_Engi_bot

Прочитай docs/sessions/2026-05-25_deploy-and-arbitr.md

СТАТУС (всё работает):
✅ Leviathan Agent v3.1  — порт 8200, 14 Gemini + 5 Groq + Mistral
✅ ArbitrCockpit v0.5    — порт 8095, leviathanstory.ru/arbitr/
✅ Groq fallback         — mark_dead + _groq_fallback задеплоены

ЗАДАЧИ (приоритет):
1. 🔴 Тест ArbitrCockpit pipeline через агента:
   Отправь в @Levi_Engi_bot:
   "/task Зайди на http://localhost:8095/api/orders и покажи список заказов"

2. 🔴 Тест Groq fallback:
   ssh levi "curl -s http://localhost:8200/health" — убедись что groq есть
   Потом заблокируй все Gemini ключи временно и отправь задачу

3. 🟡 Выяснить причину 403 на Gemini ключах:
   ssh levi "cd /opt/leviathan_engine/agent_service && \
     python3 -c \"
   import urllib.request, json
   key = open('.env').read()
   import re; keys = re.findall(r'GEMINI_K\d+=(.+)', key)
   k = keys[0].strip()
   url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={k}'
   data = json.dumps({'contents':[{'parts':[{'text':'ping'}]}]}).encode()
   req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'})
   try:
       r = urllib.request.urlopen(req, timeout=10)
       print('OK:', r.status)
   except Exception as e:
       print('ERROR:', e)
   \""

4. 🟢 Удалить старый репо:
   ssh levi "rm -rf /root/Leviathan_Agent/"

5. 🟢 React дашборд (вынести из main.py)
```

---

## Полезные команды

```bash
# Статус всего
ssh levi "systemctl status leviathan_agent arbitr --no-pager | grep Active"

# Тест агента
ssh levi "curl -s -X POST http://localhost:8200/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{\"prompt\": \"покажи uptime\", \"mode\": \"NORMAL\"}'"

# ArbitrCockpit
curl https://leviathanstory.ru/arbitr/  # пароль: arbitr2026

# Логи live
ssh levi "journalctl -u leviathan_agent -f"
```
