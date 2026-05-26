"""
Concept mapper — emergent concept discovery and taxonomy generation.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class ConceptMapper:
    """Discover concepts and maintain taxonomy."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS concept_taxonomy (
                id              TEXT PRIMARY KEY,
                concept_name    TEXT NOT NULL,
                parent_id       TEXT,
                level           INTEGER NOT NULL DEFAULT 0,
                confidence      REAL NOT NULL DEFAULT 0.5,
                metadata        TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS strategic_insights (
                id              TEXT PRIMARY KEY,
                domain          TEXT NOT NULL DEFAULT 'general',
                insight         TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                evidence        TEXT DEFAULT '',
                execution_id    TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.commit()

    @classmethod
    async def discover_concepts(
        cls,
        text: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        await cls.ensure_tables()
        entities = EntityMemory.extract_entity_candidates(text)
        discovered: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        for name in entities[:8]:
            conf = 0.55
            if not MemorySafety.world_model_confidence_ok(conf):
                continue
            cursor = await db.execute(
                "SELECT id FROM concept_taxonomy WHERE lower(concept_name) = lower(?) LIMIT 1",
                (name,),
            )
            if await cursor.fetchone():
                continue

            cid = f"cpt-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO concept_taxonomy
                   (id, concept_name, parent_id, level, confidence, metadata, created_at)
                   VALUES (?, ?, NULL, 0, ?, '{}', ?)""",
                (cid, name, conf, now),
            )
            discovered.append({"id": cid, "conceptName": name, "confidence": conf})
            if emit_fn:
                await emit_fn(
                    execution_id,
                    "concept_discovered",
                    "intelligence",
                    f"Concept discovered: {name}",
                    conceptId=cid,
                    conceptName=name,
                )

        await db.commit()
        return discovered

    @classmethod
    async def generate_taxonomy_from_entities(cls, limit: int = 30) -> dict[str, Any]:
        await cls.ensure_tables()
        graph = await EntityMemory.get_graph(limit=limit)
        entity_types = Counter(e.get("entityType", "concept") for e in graph.get("entities", []))

        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        root_id = f"cpt-root-{uuid.uuid4().hex[:8]}"
        await db.execute(
            """INSERT OR IGNORE INTO concept_taxonomy
               (id, concept_name, parent_id, level, confidence, metadata, created_at)
               VALUES (?, 'Knowledge Base', NULL, -1, 1.0, '{}', ?)""",
            (root_id, now),
        )

        for etype, count in entity_types.most_common(5):
            tid = f"cpt-type-{uuid.uuid4().hex[:8]}"
            await db.execute(
                """INSERT INTO concept_taxonomy
                   (id, concept_name, parent_id, level, confidence, metadata, created_at)
                   VALUES (?, ?, ?, 0, 0.7, ?, ?)""",
                (tid, etype, root_id, json.dumps({"entityCount": count}), now),
            )

        await db.commit()
        return {"rootId": root_id, "typeNodes": len(entity_types)}

    @classmethod
    async def record_strategic_insight(
        cls,
        domain: str,
        insight: str,
        *,
        confidence: float = 0.6,
        evidence: str = "",
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any] | None:
        if not settings.enable_vector_memory or not insight.strip():
            return None
        if confidence < MemorySafety.min_inference_confidence():
            return None

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        iid = f"ins-{uuid.uuid4().hex[:12]}"
        db = await get_db()
        await db.execute(
            """INSERT INTO strategic_insights
               (id, domain, insight, confidence, evidence, execution_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (iid, domain[:60], insight[:800], confidence, evidence[:400], execution_id, now),
        )
        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "strategic_insight_generated",
                "intelligence",
                insight[:100],
                insightId=iid,
                domain=domain,
                confidence=confidence,
            )
        return {"id": iid, "domain": domain, "insight": insight, "confidence": confidence}

    @classmethod
    async def get_insights(cls, limit: int = 20) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, domain, insight, confidence, created_at
               FROM strategic_insights ORDER BY confidence DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "domain": r["domain"],
                "insight": r["insight"],
                "confidence": r["confidence"],
                "createdAt": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
