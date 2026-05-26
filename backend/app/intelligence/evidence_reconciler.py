"""
Evidence reconciler — consensus fact selection and source reliability weighting.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.belief_tracker import BeliefTracker
from app.intelligence.conflict_resolver import ConflictResolver
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class EvidenceReconciler:
    """Reconcile multi-source evidence into consensus facts."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS evidence_consensus (
                id              TEXT PRIMARY KEY,
                topic           TEXT NOT NULL,
                consensus_claim TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                source_scores   TEXT NOT NULL DEFAULT '{}',
                supporting_count INTEGER NOT NULL DEFAULT 0,
                conflicting_count INTEGER NOT NULL DEFAULT 0,
                execution_id    TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.commit()

    @classmethod
    def _source_weight(cls, source_id: str, reliability_map: dict[str, float]) -> float:
        return reliability_map.get(source_id, 0.6)

    @classmethod
    def _evidence_conflict_score(cls, claims: list[str]) -> float:
        if len(claims) < 2:
            return 0.0
        return ConflictResolver._conflict_score(claims[0], claims[1])

    @classmethod
    async def reconcile_claims(
        cls,
        topic: str,
        claims: list[dict[str, Any]],
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory or not claims:
            return {"consensus": None}

        await cls.ensure_tables()
        reliability: dict[str, float] = {}
        weighted: list[tuple[str, float]] = []

        for c in claims:
            text = str(c.get("text", c.get("claim", "")))
            src = str(c.get("sourceId", c.get("source", "unknown")))
            rel = float(c.get("reliability", c.get("confidence", 0.6)))
            reliability[src] = max(reliability.get(src, 0), rel)
            w = cls._source_weight(src, reliability) * rel
            weighted.append((text, w))

        if not weighted:
            return {"consensus": None}

        best = max(weighted, key=lambda x: x[1])
        conflict_score = cls._evidence_conflict_score([t for t, _ in weighted[:2]])
        supporting = sum(1 for t, w in weighted if w >= best[1] * 0.8)
        conflicting = len(weighted) - supporting

        if conflicting > 0 and conflict_score > 0.4:
            texts = [t for t, _ in weighted]
            await ConflictResolver.register_conflict(
                topic, texts[0], texts[-1] if len(texts) > 1 else texts[0],
                execution_id=execution_id, emit_fn=emit_fn,
            )

        if best[1] < MemorySafety.min_inference_confidence():
            return {"consensus": None, "suppressed": True, "reason": "low_confidence"}

        now = datetime.now(timezone.utc).isoformat()
        cid = f"ecs-{uuid.uuid4().hex[:12]}"
        db = await get_db()
        import json
        await db.execute(
            """INSERT INTO evidence_consensus
               (id, topic, consensus_claim, confidence, source_scores,
                supporting_count, conflicting_count, execution_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid, topic[:120], best[0][:500], best[1],
                json.dumps(reliability), supporting, conflicting, execution_id, now,
            ),
        )
        await db.commit()

        await BeliefTracker.propagate_from_evidence(
            topic, supporting, conflicting,
            execution_id=execution_id, emit_fn=emit_fn,
        )

        return {
            "consensusId": cid,
            "topic": topic,
            "consensusClaim": best[0],
            "confidence": round(best[1], 3),
            "conflictScore": round(conflict_score, 3),
            "supportingCount": supporting,
            "conflictingCount": conflicting,
        }
