"""
Pattern generalizer — extract reusable execution patterns from runs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class PatternGeneralizer:
    """Extract and store successful execution patterns."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS execution_patterns (
                id              TEXT PRIMARY KEY,
                pattern_type    TEXT NOT NULL,
                task_signature  TEXT NOT NULL DEFAULT '',
                pattern_data    TEXT NOT NULL DEFAULT '{}',
                success_rate    REAL NOT NULL DEFAULT 0.5,
                use_count       INTEGER NOT NULL DEFAULT 0,
                source_execution_id TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS heuristic_evolution (
                id              TEXT PRIMARY KEY,
                heuristic_key   TEXT NOT NULL,
                old_value       REAL NOT NULL DEFAULT 0.5,
                new_value       REAL NOT NULL DEFAULT 0.5,
                change_reason   TEXT DEFAULT '',
                source_execution_id TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.commit()

    @classmethod
    def task_signature(cls, objective: str) -> str:
        tokens = sorted(set(objective.lower().split()))[:25]
        return " ".join(tokens)[:300]

    @classmethod
    async def extract_from_execution(
        cls,
        execution_id: str,
        objective: str,
        workflow_memory: dict[str, Any],
        *,
        status: str = "completed",
        emit_fn: EmitFn | None = None,
    ) -> list[str]:
        if not settings.enable_vector_memory:
            return []

        await cls.ensure_tables()
        patterns: list[str] = []
        sig = cls.task_signature(objective)
        now = datetime.now(timezone.utc).isoformat()
        success = status == "completed"

        strategy = workflow_memory.get("selected_strategy") or {}
        if strategy:
            pid = await cls._store_pattern(
                "strategy",
                sig,
                {
                    "strategyLabel": strategy.get("label"),
                    "approach": strategy.get("approach"),
                    "compositeScore": strategy.get("compositeScore"),
                },
                success,
                execution_id,
                now,
            )
            patterns.append(pid)

        tool_calls = workflow_memory.get("tool_calls") or []
        if isinstance(tool_calls, list) and tool_calls:
            tools_used = [c.get("tool_name") for c in tool_calls if c.get("status") == "completed"]
            if tools_used:
                pid = await cls._store_pattern(
                    "tool_chain",
                    sig,
                    {"tools": tools_used[:10], "count": len(tools_used)},
                    success,
                    execution_id,
                    now,
                )
                patterns.append(pid)

        retry_count = int(workflow_memory.get("global_retry_count") or 0)
        if retry_count > 0:
            pid = await cls._store_pattern(
                "retry_recovery",
                sig,
                {"retryCount": retry_count, "recovered": success},
                success,
                execution_id,
                now,
            )
            patterns.append(pid)

        if emit_fn and patterns:
            await emit_fn(
                execution_id,
                "long_term_pattern_detected",
                "intelligence",
                f"Detected {len(patterns)} execution pattern(s)",
                patternIds=patterns,
                patternTypes=["strategy", "tool_chain", "retry_recovery"],
            )

        return patterns

    @classmethod
    async def _store_pattern(
        cls,
        pattern_type: str,
        sig: str,
        data: dict[str, Any],
        success: bool,
        execution_id: str,
        now: str,
    ) -> str:
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, success_rate, use_count FROM execution_patterns
               WHERE pattern_type = ? AND task_signature = ?
               LIMIT 1""",
            (pattern_type, sig),
        )
        row = await cursor.fetchone()

        if row:
            pid = row["id"]
            count = row["use_count"] + 1
            rate = (row["success_rate"] * row["use_count"] + (1.0 if success else 0.0)) / count
            await db.execute(
                """UPDATE execution_patterns SET pattern_data = ?, success_rate = ?,
                   use_count = ?, updated_at = ? WHERE id = ?""",
                (json.dumps(data), rate, count, now, pid),
            )
        else:
            pid = f"pat-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO execution_patterns
                   (id, pattern_type, task_signature, pattern_data, success_rate,
                    source_execution_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    pattern_type,
                    sig,
                    json.dumps(data),
                    1.0 if success else 0.0,
                    execution_id,
                    now,
                    now,
                ),
            )

        await db.commit()
        return pid

    @classmethod
    async def record_heuristic_change(
        cls,
        heuristic_key: str,
        old_value: float,
        new_value: float,
        *,
        reason: str = "",
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> str:
        from app.intelligence.memory_safety import MemorySafety

        if not MemorySafety.heuristic_evolution_allowed(old_value, new_value):
            return ""

        await cls.ensure_tables()
        hid = f"he-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            """INSERT INTO heuristic_evolution
               (id, heuristic_key, old_value, new_value, change_reason,
                source_execution_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hid, heuristic_key, old_value, new_value, reason, execution_id, now),
        )
        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "heuristic_evolved",
                "intelligence",
                f"Heuristic {heuristic_key}: {old_value:.2f} → {new_value:.2f}",
                heuristicKey=heuristic_key,
                oldValue=old_value,
                newValue=new_value,
            )
        return hid

    @classmethod
    async def get_patterns_for_objective(cls, objective: str, limit: int = 5) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        sig = cls.task_signature(objective)
        sig_tokens = set(sig.split())
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM execution_patterns ORDER BY success_rate DESC LIMIT 50"
        )
        rows = await cursor.fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            row_tokens = set((row["task_signature"] or "").split())
            overlap = len(sig_tokens & row_tokens) / max(len(sig_tokens), 1)
            score = float(row["success_rate"]) * 0.6 + overlap * 0.4
            scored.append(
                (
                    score,
                    {
                        "id": row["id"],
                        "patternType": row["pattern_type"],
                        "patternData": json.loads(row["pattern_data"] or "{}"),
                        "successRate": row["success_rate"],
                        "score": round(score, 3),
                    },
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]
