"""
agent/solution_engine.py — Инструмент агента для оптимального решения.

Цепочка (шаги SolutionEngine):
  1. RouteIndex.match()  → есть паттерн → выбрать вариант по контексту
  2. Если нет — PyPI + GitHub поиск (http_get)
  3. Параллельная генерация 2 вариантов (min-max)
  4. Матрица рисков → отсечь BLOCK
  5. Выбор оптимального варианта
  6. RouteIndex.add()  → сохранить новый паттерн
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from db.route_index import RouteIndex, RouteMatch

logger = logging.getLogger("solution_engine")


@dataclass
class SolutionResult:
    """Pezuльтат поиска оптимального решения."""
    source:      str          # "route_index" | "pypi" | "github" | "generated"
    variant_key: str          # a | b | c
    name:        str
    description: str
    risk_score:  int
    risk_label:  str
    code_ref:    str = ""
    confidence:  float = 0.5
    route_match: Optional[object] = None  # RouteMatch если нашло в KB
    all_variants: list = field(default_factory=list)

    def to_agent_text(self) -> str:
        """Textовое представление для агента (FC loop получает это)."""
        lines = [
            f"Оптимальное решение [{self.source}]: **{self.name}**",
            f"Риск: {self.risk_label} ({self.risk_score})",
            f"Описание: {self.description}",
        ]
        if self.code_ref:
            lines.append(f"Ссылка: {self.code_ref}")
        if self.all_variants:
            lines.append("\nВсе варианты:")
            for v in self.all_variants:
                lines.append(f"  [{v.get('id','?')}] {v.get('name','')} — {v.get('when','')}")
        return "\n".join(lines)


class SolutionEngine:
    """
    Оптимальный поиск решения для задачи.
    Используется как инструмент агента (явный tool call) или
    автоматически в начале _run_gemini_loop().
    """

    def __init__(self, route_index: "RouteIndex", llm_pool=None) -> None:
        self.route_index = route_index
        self.llm_pool    = llm_pool  # LLMProviderPool для генерации вариантов

    async def solve(
        self,
        task_text:   str,
        mode:        str = "personal",
        context:     Optional[str] = None,
    ) -> SolutionResult:
        """
        Основной метод. Выполняет все 6 шагов.
        Возвращает SolutionResult с лучшим вариантом.
        """
        from db.route_index import calculate_risk, MODE_RISK_LIMITS

        # ШАГ 1: RouteIndex
        match = await self.route_index.match(task_text, mode=mode)
        if match and match.variants:
            best = self._pick_variant(match.variants, mode, context)
            return SolutionResult(
                source      = "route_index",
                variant_key = best.variant_id,
                name        = best.name,
                description = f"{match.pattern} (домен: {match.domain})",
                risk_score  = best.risk_score,
                risk_label  = self._risk_label(best.risk_score),
                code_ref    = best.code_ref,
                confidence  = match.confidence,
                route_match = match,
                all_variants= [
                    {"id": v.variant_id, "name": v.name, "when": v.when}
                    for v in match.variants
                ],
            )
        elif match:
            # Паттерн нашёл, вариантов нет
            return SolutionResult(
                source      = "route_index",
                variant_key = "a",
                name        = match.pattern,
                description = f"Кновпа найдена, db={match.db_name} id={match.record_id}",
                risk_score  = 0,
                risk_label  = "🟢 LOW",
                confidence  = match.confidence,
                route_match = match,
            )

        # ШАГ 2-3: Внешний поиск + генерация
        candidates = await asyncio.gather(
            self._search_pypi(task_text),
            self._search_github(task_text),
            self._generate_variants(task_text, mode, context),
            return_exceptions=True,
        )
        all_vars: list[dict] = []
        for result in candidates:
            if isinstance(result, Exception):
                continue
            if isinstance(result, list):
                all_vars.extend(result)

        # ШАГ 4: Матрица рисков
        risk_limit = MODE_RISK_LIMITS.get(mode, 14)
        valid = []
        for v in all_vars:
            score, label = calculate_risk(
                v.get("tech_factors", []),
                v.get("ethical_factors", []),
                mode,
            )
            v["risk_score"] = score
            v["risk_label"] = label
            if score <= risk_limit:
                valid.append(v)

        if not valid:
            valid = all_vars[:2] if all_vars else [{"id": "fallback", "name": "Генерация LLM",
                                                    "risk_score": 0, "risk_label": "🟢 LOW",
                                                    "when": "всегда"}]

        # ШАГ 5: Выбор оптимального
        best_v = min(valid, key=lambda v: v.get("risk_score", 99))

        # ШАГ 6: Сохраняем новый паттерн
        route_id = await self.route_index.add(
            pattern   = task_text[:200],
            db_name   = "solution_engine",
            record_id = best_v.get("id", "a"),
            mode      = mode,
            maturity  = "raw",
            variants  = valid[:3],
        )
        logger.info("SolutionEngine: новый паттерн #%d сохранён", route_id)

        return SolutionResult(
            source      = "generated",
            variant_key = best_v.get("id", "a"),
            name        = best_v.get("name", "Генерация"),
            description = best_v.get("description", ""),
            risk_score  = best_v.get("risk_score", 0),
            risk_label  = best_v.get("risk_label", "🟢 LOW"),
            confidence  = 0.5,
            all_variants= [{"id": v.get("id","?"), "name": v.get("name",""), "when": v.get("when","")}
                           for v in valid],
        )

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _risk_label(score: int) -> str:
        from db.route_index import RISK_LABELS
        return next(lbl for limit, lbl in RISK_LABELS if score <= limit)

    @staticmethod
    def _pick_variant(variants: list, mode: str, context: Optional[str]):
        """Vybiraet nailuchshiy variant po mode i kontekstu."""
        from db.route_index import MODE_RISK_LIMITS
        limit = MODE_RISK_LIMITS.get(mode, 14)
        valid = [v for v in variants if v.risk_score <= limit]
        if not valid:
            return variants[0]
        # Предпочтительно то, что чаще успешно
        return max(valid, key=lambda v: v.success_count - v.fail_count)

    async def _search_pypi(self, query: str) -> list[dict]:
        """Searchи PyPI по запросу."""
        import httpx
        q = query.strip()[:60].replace(" ", "+")
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(f"https://pypi.org/search/?q={q}&format=json")
            if r.status_code == 200:
                data = r.json()
                items = data.get("results", [])[:3]
                return [
                    {
                        "id": f"pypi_{i}",
                        "name": it.get("name", ""),
                        "description": it.get("description", "")[:100],
                        "code_ref": f"pip install {it.get('name', '')}",
                        "tech_factors": ["external_lib"],
                        "ethical_factors": [],
                        "when": "есть готовое решение",
                    }
                    for i, it in enumerate(items)
                ]
        except Exception as e:
            logger.debug("PyPI ошибка: %s", e)
        return []

    async def _search_github(self, query: str) -> list[dict]:
        """Searchи GitHub Code."""
        import httpx
        q = query.strip()[:60]
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=2",
                    headers={"Accept": "application/vnd.github+json"},
                )
            if r.status_code == 200:
                items = r.json().get("items", [])[:2]
                return [
                    {
                        "id": f"gh_{i}",
                        "name": it.get("name", ""),
                        "description": it.get("description", "")[:100],
                        "code_ref": it.get("html_url", ""),
                        "tech_factors": ["external_lib"],
                        "ethical_factors": [],
                        "when": f"звезды: {it.get('stargazers_count', 0)}",
                    }
                    for i, it in enumerate(items)
                ]
        except Exception as e:
            logger.debug("GitHub ошибка: %s", e)
        return []

    async def _generate_variants(self, task_text: str, mode: str,
                                  context: Optional[str]) -> list[dict]:
        """Gенерирует 2 варианта через LLM: minimal + full."""
        if not self.llm_pool:
            return []
        prompt = (
            f"Задача: {task_text[:300]}\n\n"
            "JSON двух вариантов [{{'id':..., 'name':..., 'description':..., "
            "'tech_factors':[], 'ethical_factors':[], 'when':...}}].\n"
            "Ответь только JSON, без текста."
        )
        try:
            text = await self.llm_pool.complete(prompt, max_tokens=300)
            import json, re
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            logger.debug("generate_variants ошибка: %s", e)
        return [
            {"id": "minimal", "name": "minimal решение",
             "description": task_text[:80], "tech_factors": [], "ethical_factors": [],
             "when": "MVP"},
        ]


# ── Tool для агента ───────────────────────────────────────────────

async def solve_task_tool(
    task:  str,
    mode:  str = "personal",
    context: str = "",
    _engine: Optional[SolutionEngine] = None,
) -> dict:
    """
    Tool для FC-loop агента. Вызывается явно:
      {"tool": "solve_task", "args": {"task": "...", "mode": "personal"}}
    """
    if _engine is None:
        return {"error": "SolutionEngine не инициализирован"}
    result = await _engine.solve(task, mode=mode, context=context or None)
    return {
        "solution":   result.name,
        "source":     result.source,
        "risk":       result.risk_label,
        "confidence": result.confidence,
        "text":       result.to_agent_text(),
        "variants":   result.all_variants,
    }