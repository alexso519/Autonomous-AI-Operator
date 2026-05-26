"""
Timeline reasoner — temporal reasoning over facts and entities.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.fact_evolution import FactEvolution

logger = logging.getLogger(__name__)


class TimelineReasoner:
    """Temporal queries and knowledge freshness scoring."""

    @classmethod
    async def get_knowledge_timeline(
        cls,
        *,
        limit: int = 30,
        subject_filter: str = "",
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        await FactEvolution.ensure_tables()
        db = await get_db()

        if subject_filter:
            cursor = await db.execute(
                """SELECT fv.fact_id, fv.version, fv.object_value, fv.confidence,
                          fv.change_reason, fv.created_at,
                          kf.subject, kf.predicate
                   FROM fact_versions fv
                   JOIN knowledge_facts kf ON kf.id = fv.fact_id
                   WHERE lower(kf.subject) LIKE lower(?)
                   ORDER BY fv.created_at DESC LIMIT ?""",
                (f"%{subject_filter}%", limit),
            )
        else:
            cursor = await db.execute(
                """SELECT fv.fact_id, fv.version, fv.object_value, fv.confidence,
                          fv.change_reason, fv.created_at,
                          kf.subject, kf.predicate
                   FROM fact_versions fv
                   JOIN knowledge_facts kf ON kf.id = fv.fact_id
                   ORDER BY fv.created_at DESC LIMIT ?""",
                (limit,),
            )

        return [
            {
                "factId": r["fact_id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "version": r["version"],
                "objectValue": r["object_value"],
                "confidence": r["confidence"],
                "changeReason": r["change_reason"],
                "timestamp": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]

    @classmethod
    async def compute_freshness_scores(cls) -> int:
        """Decay freshness for stale facts."""
        await FactEvolution.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """UPDATE knowledge_facts SET freshness_score = MAX(0.1,
                   freshness_score * 0.98),
               confidence = MAX(0.1, confidence * 0.99)
               WHERE updated_at < datetime('now', '-14 days')"""
        )
        await db.commit()
        return cursor.rowcount

    @classmethod
    async def temporal_context_block(cls, subject: str) -> str:
        facts = await FactEvolution.get_facts_for_subject(subject)
        if not facts:
            return ""
        lines = [f"[World model — {subject}]"]
        for f in facts[:5]:
            fresh = f.get("confidence", 0)
            lines.append(
                f"- {f['subject']} {f['predicate']} {f['objectValue']} "
                f"(conf={fresh:.2f}, status={f.get('status', 'active')})"
            )
        return "\n".join(lines)

    @classmethod
    async def recent_world_model_events(cls, limit: int = 20) -> list[dict[str, Any]]:
        await FactEvolution.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, subject, object_value, confidence, status, updated_at
               FROM knowledge_facts ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "subject": r["subject"],
                "objectValue": r["object_value"],
                "confidence": r["confidence"],
                "status": r["status"],
                "updatedAt": r["updated_at"],
            }
            for r in await cursor.fetchall()
        ]
