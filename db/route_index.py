"""
db/route_index.py — Индексный узел графа знаний.

Архитектура:
  агент получил задачу
    ↓
  RouteIndex.match(task_text)
    → нашёл паттерн: (db_name, record_id, next_hop?, confidence)
    → не нашёл: None → новая ветка (SolutionEngine)
  
  После решения:
  RouteIndex.add(pattern)
    → maturity: raw → tested → stable

SQL-схема:
  routes: каждый паттерн с указателем на базу+запись
  variants: варианты решения для одного паттерна
  route_stats: статистика использования маршрутов
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger("route_index")

DB_PATH = "db/route_index.db"

# ── Датаклассы ─────────────────────────────────────────────────────

@dataclass
class RouteMatch:
    """Peзультат поиска паттерна."""
    route_id:    int
    pattern:     str
    db_name:     str            # база где хранится знание
    record_id:   str            # id записи в базе
    confidence:  float          # 0.0–1.0
    domain:      str = ""
    mode:        str = "personal"
    maturity:    str = "raw"    # raw | tested | stable
    next_hop_db: str = ""       # следующий узел если нужно больше
    next_hop_id: str = ""
    variants:    list = field(default_factory=list)


@dataclass
class RouteVariant:
    """Oдин вариант решения."""
    variant_id:  str            # a | b | c
    name:        str
    complexity:  str            # low | mid | high
    risk_score:  int            # 0–15
    when:        str            # когда выбрать
    code_ref:    str = ""       # path или inline snippet
    success_count: int = 0
    fail_count:  int = 0


# ── Матрица рисков ───────────────────────────────────────────

RISK_FACTORS_TECHNICAL = {
    "subprocess":  3,
    "shell":        3,
    "network":      2,
    "recursion":    2,
    "external_lib": 1,
    "disk_write":   1,
}

RISK_FACTORS_ETHICAL = {
    # factor        personal  freelancer  public
    "torrent":      (1,  5,  8),
    "scraping":     (1,  3,  5),
    "personal_data":(2,  5,  9),
    "bypass":       (2,  6,  9),
    "copyright":    (1,  4,  8),
}

MODE_RISK_LIMITS = {"personal": 14, "freelancer": 9, "public": 4}
MODE_IDX         = {"personal":  0,  "freelancer": 1, "public": 2}

RISK_LABELS = [(4, "🟢 LOW"), (9, "🟡 MEDIUM"), (14, "🔴 HIGH"), (999, "⛔ BLOCK")]


def calculate_risk(tech_factors: list[str], ethical_factors: list[str],
                   mode: str = "personal") -> tuple[int, str]:
    """Pассчитать риск варианта для указанного mode."""
    score = sum(RISK_FACTORS_TECHNICAL.get(f, 0) for f in tech_factors)
    mi    = MODE_IDX.get(mode, 0)
    score += sum(RISK_FACTORS_ETHICAL.get(f, (0, 0, 0))[mi] for f in ethical_factors)
    label = next(lbl for limit, lbl in RISK_LABELS if score <= limit)
    return score, label


# ── RouteIndex ───────────────────────────────────────────────────

class RouteIndex:
    """
    Индексный узел графа знаний. Хранит маршруты:
        паттерн → (база, id, следующий_узел)
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Cоздаёт таблицы (ALTER TABLE без DROP)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS routes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_hash TEXT    NOT NULL UNIQUE,
                    pattern      TEXT    NOT NULL,
                    keywords     TEXT    NOT NULL DEFAULT '[]',
                    domain       TEXT    NOT NULL DEFAULT 'general',
                    db_name      TEXT    NOT NULL,
                    record_id    TEXT    NOT NULL,
                    next_hop_db  TEXT    NOT NULL DEFAULT '',
                    next_hop_id  TEXT    NOT NULL DEFAULT '',
                    mode         TEXT    NOT NULL DEFAULT 'personal',
                    maturity     TEXT    NOT NULL DEFAULT 'raw',
                    confidence   REAL    NOT NULL DEFAULT 0.5,
                    use_count    INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    created_at   REAL    NOT NULL,
                    updated_at   REAL    NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS variants (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    route_id     INTEGER NOT NULL REFERENCES routes(id),
                    variant_key  TEXT    NOT NULL,
                    name         TEXT    NOT NULL,
                    complexity   TEXT    NOT NULL DEFAULT 'mid',
                    risk_score   INTEGER NOT NULL DEFAULT 0,
                    when_to_use  TEXT    NOT NULL DEFAULT '',
                    code_ref     TEXT    NOT NULL DEFAULT '',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count   INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(route_id, variant_key)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS route_stats (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    route_id   INTEGER NOT NULL REFERENCES routes(id),
                    task_id    TEXT    NOT NULL,
                    hit        INTEGER NOT NULL DEFAULT 1,
                    outcome    TEXT    NOT NULL DEFAULT 'unknown',
                    ts         REAL    NOT NULL
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_routes_domain ON routes(domain)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_routes_mode ON routes(mode)"
            )
            await db.commit()
            logger.info("RouteIndex: инициализирован (%s)", self.db_path)

    # ── Хеширование и ключевые слова ────────────────────────

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Pростое извлечение ключевых слов без embedding."""
        import re
        stopwords = {
            "сделай", "создай", "напиши", "дай", "помоги",
            "make", "create", "build", "write", "add", "fix", "the", "and",
        }
        words = re.findall(r'[a-zа-я]{3,}', text.lower())
        return [w for w in words if w not in stopwords][:20]

    # ── match ───────────────────────────────────────────────────────────────

    async def match(
        self,
        task_text: str,
        mode:      str = "personal",
        min_confidence: float = 0.3,
    ) -> Optional[RouteMatch]:
        """
        Находит наиболее подходящий маршрут для задачи.
        
        Стратегия:
          1. Точное совпадение хеша (confidence=1.0)
          2. Пересечение ключевых слов (confidence = matches/total)
        """
        keywords = self._extract_keywords(task_text)
        phash    = self._hash(task_text)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # 1. Точное совпадение хеша
            async with db.execute(
                "SELECT * FROM routes WHERE pattern_hash=? AND mode IN (?, 'personal')",
                (phash, mode),
            ) as cur:
                row = await cur.fetchone()
            if row:
                return await self._row_to_match(db, row, confidence=1.0)

            # 2. Пересечение ключевых слов
            best_row, best_score = None, 0.0
            async with db.execute(
                "SELECT * FROM routes WHERE mode IN (?, 'personal') ORDER BY use_count DESC LIMIT 200",
                (mode,),
            ) as cur:
                rows = await cur.fetchall()

            for row in rows:
                stored_kw = json.loads(row["keywords"] or "[]")
                if not stored_kw:
                    continue
                hits = len(set(keywords) & set(stored_kw))
                score = hits / max(len(stored_kw), 1)
                if score > best_score:
                    best_score, best_row = score, row

            if best_row and best_score >= min_confidence:
                return await self._row_to_match(db, best_row, confidence=best_score)

        return None

    async def _row_to_match(
        self, db: aiosqlite.Connection, row, confidence: float
    ) -> RouteMatch:
        """Kонвертирует строку БД в RouteMatch."""
        async with db.execute(
            "SELECT * FROM variants WHERE route_id=? ORDER BY success_count DESC",
            (row["id"],),
        ) as cur:
            vrows = await cur.fetchall()
        variants = [
            RouteVariant(
                variant_id=v["variant_key"], name=v["name"],
                complexity=v["complexity"],  risk_score=v["risk_score"],
                when=v["when_to_use"],        code_ref=v["code_ref"],
                success_count=v["success_count"], fail_count=v["fail_count"],
            )
            for v in vrows
        ]
        return RouteMatch(
            route_id   = row["id"],
            pattern    = row["pattern"],
            db_name    = row["db_name"],
            record_id  = row["record_id"],
            confidence = confidence,
            domain     = row["domain"],
            mode       = row["mode"],
            maturity   = row["maturity"],
            next_hop_db=row["next_hop_db"],
            next_hop_id=row["next_hop_id"],
            variants   = variants,
        )

    # ── add / update ───────────────────────────────────────────────────

    async def add(
        self,
        pattern:     str,
        db_name:     str,
        record_id:   str,
        domain:      str = "general",
        mode:        str = "personal",
        maturity:    str = "raw",
        keywords:    Optional[list[str]] = None,
        next_hop_db: str = "",
        next_hop_id: str = "",
        variants:    Optional[list[dict]] = None,
    ) -> int:
        """Dобавляет новый маршрут (UPSERT по pattern_hash)."""
        kw    = keywords or self._extract_keywords(pattern)
        phash = self._hash(pattern)
        now   = time.time()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id FROM routes WHERE pattern_hash=?", (phash,)
            ) as cur:
                existing = await cur.fetchone()

            if existing:
                route_id = existing[0]
                await db.execute(
                    """UPDATE routes
                       SET db_name=?, record_id=?, maturity=?, keywords=?,
                           next_hop_db=?, next_hop_id=?, updated_at=?
                       WHERE id=?""",
                    (db_name, record_id, maturity,
                     json.dumps(kw, ensure_ascii=False),
                     next_hop_db, next_hop_id, now, route_id),
                )
            else:
                async with db.execute(
                    """INSERT INTO routes
                       (pattern_hash, pattern, keywords, domain, db_name, record_id,
                        next_hop_db, next_hop_id, mode, maturity, confidence,
                        use_count, success_count, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,0.5,0,0,?,?)""",
                    (phash, pattern, json.dumps(kw, ensure_ascii=False),
                     domain, db_name, record_id,
                     next_hop_db, next_hop_id, mode, maturity, now, now),
                ) as cur:
                    route_id = cur.lastrowid

            # Варианты
            for v in (variants or []):
                risk_score, _ = calculate_risk(
                    v.get("tech_factors", []),
                    v.get("ethical_factors", []),
                    mode,
                )
                await db.execute(
                    """INSERT OR REPLACE INTO variants
                       (route_id, variant_key, name, complexity, risk_score,
                        when_to_use, code_ref)
                       VALUES (?,?,?,?,?,?,?)""",
                    (route_id, v.get("id", "a"), v["name"],
                     v.get("complexity", "mid"), risk_score,
                     v.get("when", ""), v.get("code_ref", "")),
                )
            await db.commit()
        logger.info("RouteIndex: добавлен маршрут #%d '%s'", route_id, pattern[:50])
        return route_id

    async def record_hit(
        self,
        route_id: int,
        task_id:  str,
        outcome:  str = "done",
    ) -> None:
        """3аписывает использование маршрута и обновляет maturity."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO route_stats (route_id, task_id, outcome, ts)
                   VALUES (?,?,?,?)""",
                (route_id, task_id, outcome, time.time()),
            )
            # use_count += 1; если успех → success_count += 1
            if outcome == "done":
                await db.execute(
                    "UPDATE routes SET use_count=use_count+1, success_count=success_count+1, updated_at=? WHERE id=?",
                    (time.time(), route_id),
                )
            else:
                await db.execute(
                    "UPDATE routes SET use_count=use_count+1, updated_at=? WHERE id=?",
                    (time.time(), route_id),
                )
            # Maturity upgrade: raw→5 усп.→tested→10 усп.→stable
            async with db.execute(
                "SELECT success_count FROM routes WHERE id=?", (route_id,)
            ) as cur:
                row = await cur.fetchone()
            sc = row[0] if row else 0
            new_maturity = "raw" if sc < 5 else ("tested" if sc < 10 else "stable")
            await db.execute(
                "UPDATE routes SET maturity=? WHERE id=?",
                (new_maturity, route_id),
            )
            await db.commit()

    async def promote_variant(
        self, route_id: int, variant_key: str, success: bool
    ) -> None:
        """Oбновляет статистику варианта."""
        col = "success_count" if success else "fail_count"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE variants SET {col}={col}+1 WHERE route_id=? AND variant_key=?",
                (route_id, variant_key),
            )
            await db.commit()

    # ── stats ────────────────────────────────────────────────────────────────

    async def stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT maturity, COUNT(*) FROM routes GROUP BY maturity"
            ) as cur:
                rows = await cur.fetchall()
            async with db.execute("SELECT COUNT(*) FROM variants") as cur:
                v_count = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT SUM(use_count), SUM(success_count) FROM routes"
            ) as cur:
                usage = await cur.fetchone()
        return {
            "routes_by_maturity": {r[0]: r[1] for r in rows},
            "total_variants": v_count,
            "total_hits":    usage[0] or 0,
            "total_successes": usage[1] or 0,
        }