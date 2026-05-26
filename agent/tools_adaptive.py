"""
agent/tools_adaptive.py - инструменты самоадаптации Leviathan Agent

Добавляет агенту способность расширять себя:
  pip_install        - установить Python пакет на лету
  pypi_search        - найти пакет на PyPI
  download_file      - скачать файл по URL
  zip_files          - упаковать файлы в ZIP
  unzip_file         - распаковать ZIP
  send_telegram_file - отправить файл в Telegram
  web_search         - поиск в DuckDuckGo (без API ключа)
"""
from __future__ import annotations
import asyncio, logging, os, zipfile
from pathlib import Path
from typing import Any
import httpx
logger = logging.getLogger("agent.tools_adaptive")

async def pip_install(package: str) -> dict:
    """Устанавливает Python-пакет через pip. Используй когда нужна библиотека которой нет в системе."""
    logger.info("pip_install: %s", package)
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", package, "--break-system-packages", "-q",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        return {"ok": proc.returncode == 0, "package": package,
                "stdout": out.decode(errors="replace")[:500],
                "stderr": err.decode(errors="replace")[:300]}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Таймаут 120с", "package": package}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def pypi_search(query: str, max_results: int = 5) -> dict:
    """Поиск пакета на PyPI. Используй перед pip_install для уточнения имени."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://pypi.org/pypi/{query}/json")
            if r.status_code == 200:
                info = r.json().get("info", {})
                return {"ok": True, "found": True, "name": info.get("name"),
                        "version": info.get("version"), "summary": info.get("summary"),
                        "install_cmd": f"pip install {info.get("name")}"}
        return {"ok": True, "found": False, "query": query}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def download_file(url: str, dest_path: str) -> dict:
    """Скачать файл по URL."""
    try:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192): f.write(chunk)
        return {"ok": True, "path": str(dest),
                "size_kb": round(dest.stat().st_size/1024,1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def zip_files(paths: list, dest_zip: str) -> dict:
    """Упаковать файлы/директории в ZIP."""
    try:
        dest = Path(dest_zip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        added = []
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                path = Path(p)
                if path.is_file():
                    zf.write(path, path.name); added.append(path.name)
                elif path.is_dir():
                    for f in path.rglob("*"):
                        if f.is_file():
                            arc=str(f.relative_to(path.parent))
                            zf.write(f,arc); added.append(arc)
        return {"ok": True, "zip_path": str(dest), "files_added": len(added),
                "size_kb": round(dest.stat().st_size/1024,1)}
    except Exception as e: return {"ok": False, "error": str(e)}

async def unzip_file(zip_path: str, dest_dir: str) -> dict:
    """Распаковать ZIP."""
    try:
        dest = Path(dest_dir); dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names=zf.namelist(); zf.extractall(dest)
        return {"ok": True, "dest": str(dest), "files_extracted": len(names)}
    except Exception as e: return {"ok": False, "error": str(e)}

async def send_telegram_file(file_path: str, caption: str = "") -> dict:
    """Отправить файл в Telegram admin-чат."""
    try:
        from config.settings import get_settings
        s = get_settings()
        if not s.tg_configured():
            return {"ok": False, "error": "Telegram not configured"}
        p = Path(file_path)
        if not p.exists():
            return {"ok": False, "error": f"File not found: {file_path}"}
        url = f"https://api.telegram.org/bot{s.TG_BOT_TOKEN}/sendDocument"
        async with httpx.AsyncClient(timeout=60) as client:
            with open(p, "rb") as f:
                resp = await client.post(url,
                    data={"chat_id": str(s.TG_ADMIN_CHAT_ID),
                          "caption": caption[:1024] if caption else p.name},
                    files={"document": (p.name, f)})
        data = resp.json()
        return {"ok": data.get("ok", False), "file": p.name,
                "size_kb": round(p.stat().st_size/1024, 1),
                "message_id": data.get("result",{}).get("message_id"),
                "error": data.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def web_search(query: str, max_results: int = 5) -> dict:
    """Поиск в интернете через DuckDuckGo. Без API ключа."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({"title": r.get("title",""),
                                 "url": r.get("href",""),
                                 "snippet": r.get("body","")})
        return {"ok": True, "query": query, "results": results, "count": len(results)}
    except ImportError:
        pass
    except Exception as e:
        logger.warning("web_search ddgs: %s", e)
    # Fallback: DDG Instant Answer API
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "LeviathanAgent/1.0"},
            timeout=15, follow_redirects=True) as client:
            r = await client.get("https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
        data = r.json()
        results = []
        if data.get("AbstractText"):
            results.append({"title": data.get("Heading", query),
                            "url": data.get("AbstractURL",""),
                            "snippet": data["AbstractText"][:400]})
        for t in data.get("RelatedTopics",[])[:max_results]:
            if isinstance(t, dict) and t.get("Text"):
                results.append({"title": t["Text"][:80], "url": t.get("FirstURL",""),
                                 "snippet": t["Text"][:300]})
        return {"ok": True, "query": query, "results": results, "method": "ddg_instant"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

ADAPTIVE_TOOLS_REGISTRY: dict = {
    "pip_install":        pip_install,
    "pypi_search":        pypi_search,
    "download_file":      download_file,
    "zip_files":          zip_files,
    "unzip_file":         unzip_file,
    "send_telegram_file": send_telegram_file,
    "web_search":         web_search,
}

ADAPTIVE_GEMINI_TOOLS = [
    {"name":"pip_install",
     "description":"Установить Python-пакет через pip. Используй когда нужна библиотека которой нет.",
     "parameters":{"type":"object","required":["package"],
         "properties":{"package":{"type":"string"}}}},
    {"name":"pypi_search",
     "description":"Найти Python-пакет на PyPI.",
     "parameters":{"type":"object","required":["query"],
         "properties":{"query":{"type":"string"},"max_results":{"type":"integer"}}}},
    {"name":"download_file",
     "description":"Скачать файл по URL.",
     "parameters":{"type":"object","required":["url","dest_path"],
         "properties":{"url":{"type":"string"},"dest_path":{"type":"string"}}}},
    {"name":"zip_files",
     "description":"Упаковать файлы/директории в ZIP.",
     "parameters":{"type":"object","required":["paths","dest_zip"],
         "properties":{"paths":{"type":"array","items":{"type":"string"}},
                       "dest_zip":{"type":"string"}}}},
    {"name":"unzip_file",
     "description":"Распаковать ZIP.",
     "parameters":{"type":"object","required":["zip_path","dest_dir"],
         "properties":{"zip_path":{"type":"string"},"dest_dir":{"type":"string"}}}},
    {"name":"send_telegram_file",
     "description":"Отправить файл в Telegram (zip, png, pdf...) в админ-чат.",
     "parameters":{"type":"object","required":["file_path"],
         "properties":{"file_path":{"type":"string"},"caption":{"type":"string"}}}},
    {"name":"web_search",
     "description":"Поиск DuckDuckGo. Без API. Документация, ошибки, пакеты.",
     "parameters":{"type":"object","required":["query"],
         "properties":{"query":{"type":"string"},"max_results":{"type":"integer"}}}},
]
