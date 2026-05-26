"""
Belief tracker — confidence propagation and uncertainty tracking.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class BeliefTracker:
    """Track belief states with confidence propagation."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS belief_states (
                id              TEXT PRIMARY KEY,
                subject         TEXT NOT NULL,
                proposition     TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                uncertainty     REAL NOT NULL DEFAULT 0.5,
                source_count    INTEGER NOT NULL DEFAULT 1,
                execution_id    TEXT DEFAULT '',
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_belief_subject ON belief_states(subject)"""
        )
        await db.commit()

    @classmethod
    async def update_belief(
        cls,
        subject: str,
        proposition: str,
        *,
        confidence_delta: float = 0.0,
        source_reliability: float = 0.7,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, confidence, uncertainty, source_count FROM belief_states
               WHERE subject = ? AND proposition = ? LIMIT 1""",
            (subject[:120], proposition[:400]),
        )
        row = await cursor.fetchone()

        if row:
            new_conf = min(1.0, max(0.05, float(row["confidence"]) + confidence_delta * source_reliability))
            new_unc = max(0.05, 1.0 - new_conf)
            new_sources = int(row["source_count"]) + 1
            await db.execute(
                """UPDATE belief_states SET confidence = ?, uncertainty = ?,
                   source_count = ?, execution_id = ?, updated_at = ? WHERE id = ?""",
                (new_conf, new_unc, new_sources, execution_id, now, row["id"]),
            )
            belief_id = row["id"]
        else:
            belief_id = f"blf-{uuid.uuid4().hex[:12]}"
            init_conf = min(1.0, max(MemorySafety.min_inference_confidence(), 0.5 + confidence_delta))
            await db.execute(
                """INSERT INTO belief_states
                   (id, subject, proposition, confidence, uncertainty, source_count,
                    execution_id, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (belief_id, subject[:120], proposition[:400], init_conf, 1.0 - init_conf, execution_id, now),
            )
            new_conf = init_conf
            new_unc = 1.0 - init_conf

        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "belief_updated",
                "intelligence",
                f"Belief updated for {subject[:40]}",
                beliefId=belief_id,
                confidence=round(new_conf, 3),
                uncertainty=round(new_unc, 3),
            )

        return {
            "beliefId": belief_id,
            "subject": subject,
            "confidence": round(new_conf, 3),
            "uncertainty": round(new_unc, 3),
        }

    @classmethod
    async def propagate_from_evidence(
        cls,
        subject: str,
        supporting: int,
        conflicting: int,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        delta = 0.05 * supporting - 0.08 * conflicting
        return await cls.update_belief(
            subject,
            f"aggregate_evidence_{subject}",
            confidence_delta=delta,
            execution_id=execution_id,
            emit_fn=emit_fn,
        )

    @classmethod
    async def get_beliefs(cls, subject: str = "", limit: int = 30) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        if subject:
            cursor = await db.execute(
                """SELECT id, subject, proposition, confidence, uncertainty, source_count, updated_at
                   FROM belief_states WHERE subject = ? ORDER BY confidence DESC LIMIT ?""",
                (subject[:120], limit),
            )
        else:
            cursor = await db.execute(
                """SELECT id, subject, proposition, confidence, uncertainty, source_count, updated_at
                   FROM belief_states ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            )
        return [
            {
                "id": r["id"],
                "subject": r["subject"],
                "proposition": r["proposition"][:120],
                "confidence": r["confidence"],
                "uncertainty": r["uncertainty"],
                "sourceCount": r["source_count"],
                "updatedAt": r["updated_at"],
            }
            for r in await cursor.fetchall()
        ]
