"""
Causal engine — cause/effect inference and temporal consistency.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.memory_safety import MemorySafety
from app.intelligence.relationship_inference import RelationshipInference

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class CausalEngine:
    """Cause/effect link storage and inference."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS causal_links (
                id              TEXT PRIMARY KEY,
                cause_entity_id TEXT DEFAULT '',
                effect_entity_id TEXT DEFAULT '',
                cause_label     TEXT NOT NULL,
                effect_label    TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                evidence        TEXT DEFAULT '',
                execution_id    TEXT DEFAULT '',
                temporal_valid  INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_causal_cause ON causal_links(cause_label)"""
        )
        await db.commit()

    @classmethod
    async def infer_from_text(
        cls,
        text: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        await cls.ensure_tables()
        inferred = await RelationshipInference.infer_from_text(
            text, execution_id=execution_id, emit_fn=emit_fn,
        )
        causal: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        for rel in inferred:
            if rel.get("relationType") != "causes":
                continue
            link_id = f"cl-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO causal_links
                   (id, cause_entity_id, effect_entity_id, cause_label, effect_label,
                    confidence, evidence, execution_id, temporal_valid, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    link_id,
                    rel["sourceId"],
                    rel["targetId"],
                    rel.get("sourceId", ""),
                    rel.get("targetId", ""),
                    rel["confidence"],
                    "",
                    execution_id,
                    now,
                ),
            )
            causal.append({"id": link_id, **rel})
            if emit_fn:
                await emit_fn(
                    execution_id,
                    "causal_link_inferred",
                    "intelligence",
                    "Causal link inferred",
                    linkId=link_id,
                    confidence=rel["confidence"],
                )

        await db.commit()
        return causal

    @classmethod
    async def validate_temporal_consistency(cls, subject: str) -> dict[str, Any]:
        """Check fact timeline for temporal contradictions."""
        from app.intelligence.fact_evolution import FactEvolution

        await cls.ensure_tables()
        facts = await FactEvolution.get_facts_for_subject(subject, limit=10)
        if len(facts) < 2:
            return {"consistent": True, "conflicts": []}

        values = [f.get("objectValue", "") for f in facts]
        unique = set(v.lower().strip() for v in values if v)
        consistent = len(unique) <= 1
        return {
            "consistent": consistent,
            "conflicts": [] if consistent else [{"subject": subject, "values": list(unique)}],
            "factCount": len(facts),
        }

    @classmethod
    async def get_causal_history(cls, limit: int = 30) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, cause_entity_id, effect_entity_id, cause_label, effect_label,
                      confidence, execution_id, created_at
               FROM causal_links ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "causeEntityId": r["cause_entity_id"],
                "effectEntityId": r["effect_entity_id"],
                "confidence": r["confidence"],
                "executionId": r["execution_id"],
                "createdAt": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
