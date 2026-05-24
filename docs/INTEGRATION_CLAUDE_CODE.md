# Интеграция Leviathan Agent ↔ Claude Code

## Контекст
- Claude Code установлен на сервере (CLI инструмент)
- Leviathan Agent работает на FastAPI (порт 8200, Gemini)
- Цель: использовать Claude (Anthropic) как второй LLM-движок рядом с Gemini

---

## Архитектура интеграции

```
┌─────────────────────────────────────────────────────────┐
│                    LEVIATHAN AGENT                      │
│                     (port 8200)                         │
│                                                         │
│  LeviathanAgent (core.py)                               │
│       │                                                 │
│       ├── GeminiKeyPool (основной LLM)                  │
│       └── ClaudeAdapter (новый) ─────────────────────┐  │
│                                                      │  │
└──────────────────────────────────────────────────────┼──┘
                                                       │
                                           ┌───────────▼────────────┐
                                           │    Claude Code CLI      │
                                           │  /usr/local/bin/claude  │
                                           │                         │
                                           │  claude -p "prompt"     │
                                           │  --output-format json   │
                                           └────────────────────────┘
```

---

## План реализации

### 1. ClaudeAdapter (core_bridge/claude_adapter.py)

```python
"""
core_bridge/claude_adapter.py
Адаптер для Claude Code CLI как LLM backend.
Вызывает claude через subprocess (он установлен на сервере).
"""
import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClaudeResponse:
    text: str
    model: str = "claude-sonnet"
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: int = 0
    ok: bool = True
    error: Optional[str] = None


class ClaudeCodeAdapter:
    """
    Вызывает Claude Code CLI как subprocess.
    Требует: claude установлен, авторизован (claude auth login).
    """
    
    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        timeout: int = 120,
        max_tokens: int = 8096,
    ):
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._available: Optional[bool] = None
    
    def is_available(self) -> bool:
        """Проверяет что claude CLI доступен."""
        if self._available is None:
            self._available = shutil.which("claude") is not None
        return self._available
    
    async def call(self, prompt: str, system: str = "") -> ClaudeResponse:
        """
        Вызывает claude CLI.
        claude -p "prompt" --output-format json --model claude-sonnet-4-5
        """
        import time
        t0 = time.time()
        
        if not self.is_available():
            return ClaudeResponse(
                text="", ok=False,
                error="Claude Code CLI не найден. Установите: npm install -g @anthropic-ai/claude-code"
            )
        
        cmd = [
            "claude",
            "--print",                   # non-interactive режим
            "--output-format", "json",
            "--model", self.model,
            "--max-tokens", str(self.max_tokens),
        ]
        
        # Системный промт через --system-prompt если есть
        if system:
            cmd += ["--system-prompt", system]
        
        # Промт как последний аргумент
        cmd.append(prompt)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            latency_ms = int((time.time() - t0) * 1000)
            
            if proc.returncode != 0:
                return ClaudeResponse(
                    text="", ok=False,
                    error=stderr.decode(errors="replace")[:500],
                    latency_ms=latency_ms,
                )
            
            # Парсим JSON ответ
            try:
                data = json.loads(stdout.decode())
                # Claude Code CLI возвращает {result: "...", ...}
                text = data.get("result", data.get("content", str(data)))
                tokens_in = data.get("usage", {}).get("input_tokens", 0)
                tokens_out = data.get("usage", {}).get("output_tokens", 0)
            except json.JSONDecodeError:
                text = stdout.decode(errors="replace")
                tokens_in = tokens_out = 0
            
            return ClaudeResponse(
                text=text,
                model=self.model,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                latency_ms=latency_ms,
                ok=True,
            )
        
        except asyncio.TimeoutError:
            return ClaudeResponse(
                text="", ok=False,
                error=f"Claude CLI timeout ({self.timeout}s)",
            )
        except Exception as e:
            return ClaudeResponse(text="", ok=False, error=str(e))


# Singleton
_adapter: Optional[ClaudeCodeAdapter] = None

def get_claude_adapter() -> ClaudeCodeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = ClaudeCodeAdapter()
    return _adapter
```

### 2. Интеграция в KeyPool как fallback

Файл `core_bridge/key_pool.py` — добавить в `GeminiKeyPool.get_key()`:

```python
# В конце get_key() если все Gemini ключи исчерпаны:
async def get_key_or_claude(self) -> tuple[str, str]:
    """
    Возвращает (key, provider).
    provider: "gemini" | "claude"
    """
    try:
        key = await self.get_key()
        return key, "gemini"
    except AllExhaustedError:
        # Gemini исчерпан → fallback на Claude Code
        from core_bridge.claude_adapter import get_claude_adapter
        adapter = get_claude_adapter()
        if adapter.is_available():
            return "__claude__", "claude"
        raise
```

### 3. Обновление agent/core.py

В методе `run()` — обработка claude provider:

```python
# Вместо:
key = await self.key_pool.get_key()
model = self._build_model(key)

# Стать:
key, provider = await self.key_pool.get_key_or_claude()

if provider == "claude":
    response_text = await self._run_claude(task, messages)
    # ... обработать текстовый ответ
else:
    model = self._build_model(key)
    # ... обычный Gemini FC loop
```

### 4. Добавить в Settings

```python
# config/settings.py
CLAUDE_MODEL: str = "claude-sonnet-4-5"
CLAUDE_TIMEOUT: int = 120
USE_CLAUDE_FALLBACK: bool = True   # включить claude как fallback
```

---

## Сценарии использования

### Сценарий A: Gemini как основной, Claude как fallback
- Все 14 Gemini ключей в cooldown → агент переключается на Claude Code CLI
- Прозрачно для пользователя

### Сценарий B: Claude для специфических задач
- Добавить режим задачи `mode="CLAUDE"` → всегда использует Claude
- Например для code review, architectural decisions

### Сценарий C: Параллельный вызов (будущее)
- Отправить в Gemini и Claude одновременно
- Вернуть лучший ответ (by токен качество / длина)

---

## Проверка работоспособности

```bash
# На сервере:
claude --version                    # проверить что установлен
claude --print "Hello, test" --output-format json  # проверить вывод
which claude                        # путь к бинарнику

# Авторизация:
claude auth login                   # если не авторизован

# Проверка из Leviathan:
curl http://localhost:8200/health   # агент запущен
curl -X POST http://localhost:8200/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Echo test via claude", "mode": "CLAUDE"}'
```

---

## Важные ограничения

1. Claude Code CLI работает в non-interactive режиме через `--print`
2. Нет native function calling как у Gemini — только текстовые ответы
3. Для FC-loop через Claude нужен отдельный парсер (XML/JSON в промте)
4. Timeout на сервере рекомендован 120с (claude медленнее Gemini flash)
5. Токены считаются через `usage` поле JSON ответа

