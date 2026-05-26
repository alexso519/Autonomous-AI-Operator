"""
Strategy evolution — learn and rank successful execution strategies.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)


class StrategyEvolution:
    """Track and evolve execution strategies across sessions."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS learned_strategies (
                id              TEXT PRIMARY KEY,
                strategy_label  TEXT NOT NULL,
                task_signature  TEXT NOT NULL DEFAULT '',
                approach        TEXT NOT NULL DEFAULT '',
                success_count   INTEGER NOT NULL DEFAULT 0,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                avg_quality     REAL NOT NULL DEFAULT 0.5,
                avg_duration    REAL NOT NULL DEFAULT 0,
                composite_score REAL NOT NULL DEFAULT 0.5,
                metadata        TEXT NOT NULL DEFAULT '{}',
                last_used_at    TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_learned_strategies_signature
               ON learned_strategies(task_signature)"""
        )
        await db.commit()

    @classmethod
    def task_signature(cls, objective: str) -> str:
        tokens = sorted(set(objective.lower().split()))[:30]
        return " ".join(tokens)[:400]

    @classmethod
    async def record_outcome(
        cls,
        strategy_label: str,
        objective: str,
        *,
        success: bool,
        quality: float = 0.5,
        duration: float = 0,
        approach: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if not settings.enable_vector_memory:
            return ""

        await cls.ensure_tables()
        sig = cls.task_signature(objective)
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        cursor = await db.execute(
            """SELECT id, success_count, failure_count, avg_quality, avg_duration, composite_score
               FROM learned_strategies
               WHERE strategy_label = ? AND task_signature = ?
               LIMIT 1""",
            (strategy_label, sig),
        )
        row = await cursor.fetchone()

        if row:
            sid = row["id"]
            sc = row["success_count"] + (1 if success else 0)
            fc = row["failure_count"] + (0 if success else 1)
            total = sc + fc
            new_quality = (row["avg_quality"] * (total - 1) + quality) / total
            new_duration = (row["avg_duration"] * (total - 1) + duration) / total
            new_composite = (sc / max(total, 1)) * 0.5 + new_quality * 0.5

            if MemorySafety.heuristic_evolution_allowed(row["composite_score"], new_composite):
                await db.execute(
                    """UPDATE learned_strategies SET
                       success_count = ?, failure_count = ?,
                       avg_quality = ?, avg_duration = ?, composite_score = ?,
                       last_used_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (sc, fc, new_quality, new_duration, new_composite, now, now, sid),
                )
        else:
            sid = f"ls-{uuid.uuid4().hex[:12]}"
            composite = (0.5 if success else 0.3) * 0.5 + quality * 0.5
            await db.execute(
                """INSERT INTO learned_strategies
                   (id, strategy_label, task_signature, approach,
                    success_count, failure_count, avg_quality, avg_duration,
                    composite_score, metadata, last_used_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    strategy_label,
                    sig,
                    approach[:500],
                    1 if success else 0,
                    0 if success else 1,
                    quality,
                    duration,
                    composite,
                    json.dumps(metadata or {}),
                    now,
                    now,
                    now,
                ),
            )

        await db.commit()
        return sid

    @classmethod
    async def get_best_strategies(
        cls,
        objective: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        await cls.ensure_tables()
        sig = cls.task_signature(objective)
        sig_tokens = set(sig.split())
        db = await get_db()

        cursor = await db.execute(
            """SELECT * FROM learned_strategies
               ORDER BY composite_score DESC, success_count DESC
               LIMIT 50"""
        )
        rows = await cursor.fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            row_tokens = set((row["task_signature"] or "").split())
            overlap = len(sig_tokens & row_tokens) / max(len(sig_tokens), 1)
            score = float(row["composite_score"]) * 0.6 + overlap * 0.4
            scored.append(
                (
                    score,
                    {
                        "id": row["id"],
                        "strategyLabel": row["strategy_label"],
                        "approach": row["approach"],
                        "successCount": row["success_count"],
                        "failureCount": row["failure_count"],
                        "avgQuality": row["avg_quality"],
                        "compositeScore": round(score, 3),
                    },
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    @classmethod
    async def get_evolution_history(cls, limit: int = 20) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, strategy_label, task_signature, success_count, failure_count,
                      composite_score, updated_at
               FROM learned_strategies ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "strategyLabel": r["strategy_label"],
                "taskSignature": r["task_signature"][:80],
                "successCount": r["success_count"],
                "failureCount": r["failure_count"],
                "compositeScore": r["composite_score"],
                "updatedAt": r["updated_at"],
            }
            for r in await cursor.fetchall()
        ]
