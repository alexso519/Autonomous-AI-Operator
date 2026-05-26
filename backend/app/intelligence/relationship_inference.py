"""
Relationship inference — dynamic edge discovery from co-occurrence and patterns.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

CAUSAL_PATTERN = re.compile(
    r"(\w[\w\s]{2,40})\s+(?:causes?|leads? to|results? in)\s+(\w[\w\s]{2,40})",
    re.IGNORECASE,
)
IMPLIES_PATTERN = re.compile(
    r"(\w[\w\s]{2,40})\s+(?:implies?|means?|indicates?)\s+(\w[\w\s]{2,40})",
    re.IGNORECASE,
)


class RelationshipInference:
    """Infer new relationships from text patterns and graph proximity."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await KnowledgeGraph.ensure_tables()

    @classmethod
    async def infer_from_text(
        cls,
        text: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory or not text:
            return []

        await cls.ensure_tables()
        inferred: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        patterns = [
            (CAUSAL_PATTERN, "causes"),
            (IMPLIES_PATTERN, "implies"),
        ]

        for pattern, rel_type in patterns:
            for match in pattern.finditer(text):
                src_name, tgt_name = match.group(1).strip(), match.group(2).strip()
                if len(src_name) < 3 or len(tgt_name) < 3:
                    continue
                src_id = await EntityMemory.upsert_entity(src_name, execution_id=execution_id)
                tgt_id = await EntityMemory.upsert_entity(tgt_name, execution_id=execution_id)
                if not src_id or not tgt_id:
                    continue

                confidence = 0.55
                if not MemorySafety.world_model_confidence_ok(confidence):
                    continue

                inf_id = f"inf-{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO inferred_relationships
                       (id, source_id, target_id, relation_type, confidence,
                        inference_method, evidence, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        inf_id, src_id, tgt_id, rel_type, confidence,
                        "pattern_extraction", text[match.start():match.end()][:200], now,
                    ),
                )
                await KnowledgeGraph.upsert_edge(
                    src_id, tgt_id, rel_type,
                    weight=confidence,
                    evidence_weight=confidence,
                    evidence=text[match.start():match.end()][:200],
                    execution_id=execution_id,
                )
                await EntityMemory.link_entities(
                    src_id, tgt_id, rel_type,
                    confidence=confidence,
                    evidence=text[match.start():match.end()][:200],
                    execution_id=execution_id,
                    emit_fn=emit_fn,
                )
                inferred.append({
                    "id": inf_id,
                    "sourceId": src_id,
                    "targetId": tgt_id,
                    "relationType": rel_type,
                    "confidence": confidence,
                })

        await db.commit()
        return inferred

    @classmethod
    async def infer_transitive(
        cls,
        *,
        limit: int = 20,
    ) -> int:
        """Infer A→C when A→B and B→C exist with sufficient confidence."""
        if not settings.enable_vector_memory:
            return 0

        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT e1.source_id, e1.target_id AS mid, e2.target_id AS end_id,
                      MIN(e1.weight, e2.weight) AS conf
               FROM knowledge_graph_edges e1
               JOIN knowledge_graph_edges e2 ON e1.target_id = e2.source_id
               WHERE e1.source_id != e2.target_id
               LIMIT ?""",
            (limit * 3,),
        )
        count = 0
        for r in await cursor.fetchall():
            conf = float(r["conf"] or 0.4) * 0.85
            if conf < MemorySafety.min_inference_confidence():
                continue
            await KnowledgeGraph.upsert_edge(
                r["source_id"], r["end_id"], "inferred_transitive",
                weight=conf, evidence_weight=conf,
                evidence=f"via {r['mid']}",
            )
            count += 1
        return count

    @classmethod
    async def get_inferred(cls, limit: int = 30) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, source_id, target_id, relation_type, confidence, inference_method
               FROM inferred_relationships ORDER BY confidence DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "sourceId": r["source_id"],
                "targetId": r["target_id"],
                "relationType": r["relation_type"],
                "confidence": r["confidence"],
                "method": r["inference_method"],
            }
            for r in await cursor.fetchall()
        ]
