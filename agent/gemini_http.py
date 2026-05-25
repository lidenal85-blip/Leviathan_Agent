"""HTTP-совместимая замена google.generativeai для Gemini API"""
import httpx

class _FunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args

class _Part:
    def __init__(self, text=None, function_call=None):
        self.text          = text
        self.function_call = function_call

class _Content:
    def __init__(self, parts): self.parts = parts

class _Candidate:
    def __init__(self, content): self.content = content

class _Response:
    def __init__(self, candidates): self.candidates = candidates


def _to_api_part(p):
    """Конвертируем любой объект в формат Gemini API (camelCase)."""
    if isinstance(p, str):
        return {"text": p}
    if isinstance(p, dict):
        # snake_case → camelCase
        if "function_response" in p:
            return {"functionResponse": p["function_response"]}
        # уже в нужном формате
        return p
    # _Part объекты
    if hasattr(p, "function_call") and p.function_call:
        return {"functionCall": {
            "name": p.function_call.name,
            "args": p.function_call.args or {},
        }}
    if hasattr(p, "text") and p.text is not None:
        return {"text": p.text}
    return {"text": str(p)}


class GeminiHTTPChat:
    def __init__(self, api_key, model_name, system_instruction, tools, history=None):
        self.api_key            = api_key
        self.model_name         = model_name
        self.system_instruction = system_instruction
        self.tools              = tools
        self.history            = list(history or [])

    def send_message(self, message):
        if isinstance(message, str):
            message = [message]

        user_parts = [_to_api_part(p) for p in message]
        contents   = self.history + [{"role": "user", "parts": user_parts}]

        payload = {
            "contents": [
                {"role": c["role"], "parts": [_to_api_part(p) for p in c["parts"]]}
                for c in contents
            ],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": self.tools,
            "generationConfig": {"temperature": 0.2},
        }

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={self.api_key}"
        )

        resp = httpx.post(url, json=payload, timeout=120)

        if resp.status_code != 200:
            data = resp.json()
            err  = data.get("error", {})
            raise Exception(f"{err.get('code', resp.status_code)} {err.get('message', resp.text)}")

        data      = resp.json()
        raw_parts = data["candidates"][0]["content"]["parts"]

        parts = []
        for p in raw_parts:
            if "text" in p:
                parts.append(_Part(text=p["text"]))
            elif "functionCall" in p:
                fc = p["functionCall"]
                parts.append(_Part(function_call=_FunctionCall(
                    name=fc["name"], args=fc.get("args", {}))))

        # обновляем историю (храним в API-формате)
        self.history = contents + [{"role": "model", "parts": raw_parts}]

        return _Response([_Candidate(_Content(parts))])


class GeminiHTTPModel:
    def __init__(self, api_key, model_name, system_instruction, tools):
        self.api_key            = api_key
        self.model_name         = model_name
        self.system_instruction = system_instruction
        self.tools              = tools

    def start_chat(self, history=None):
        return GeminiHTTPChat(
            api_key=self.api_key,
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            tools=self.tools,
            history=history,
        )
