"""
Entity memory — persistent entity graph with relationship tracking.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

ENTITY_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z0-9]{2,30}(?:\s+[A-Z][a-zA-Z0-9]{2,20}){0,3})\b"
)


class EntityMemory:
    """Extract, store, and link entities across executions."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS entities (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                entity_type     TEXT NOT NULL DEFAULT 'concept',
                description     TEXT DEFAULT '',
                confidence      REAL NOT NULL DEFAULT 0.5,
                freshness_score REAL NOT NULL DEFAULT 1.0,
                metadata        TEXT NOT NULL DEFAULT '{}',
                first_seen_at   TEXT NOT NULL,
                last_seen_at    TEXT NOT NULL,
                mention_count   INTEGER NOT NULL DEFAULT 1
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS entity_relationships (
                id              TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                target_id       TEXT NOT NULL,
                relation_type   TEXT NOT NULL DEFAULT 'related_to',
                confidence      REAL NOT NULL DEFAULT 0.5,
                evidence        TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES entities(id),
                FOREIGN KEY (target_id) REFERENCES entities(id)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS execution_entities (
                id              TEXT PRIMARY KEY,
                execution_id    TEXT NOT NULL,
                entity_id       TEXT NOT NULL,
                context_snippet TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_entity_rel_source ON entity_relationships(source_id)"""
        )
        await db.commit()

    @classmethod
    def extract_entity_candidates(cls, text: str) -> list[str]:
        if not text:
            return []
        candidates = ENTITY_PATTERN.findall(text)
        seen: set[str] = set()
        result: list[str] = []
        for c in candidates:
            key = c.lower()
            if key not in seen and len(c) > 2:
                seen.add(key)
                result.append(c)
        return result[:15]

    @classmethod
    async def upsert_entity(
        cls,
        name: str,
        *,
        entity_type: str = "concept",
        description: str = "",
        confidence: float = 0.5,
        execution_id: str = "",
        context_snippet: str = "",
        emit_fn: EmitFn | None = None,
    ) -> str | None:
        if not settings.enable_vector_memory:
            return None

        await cls.ensure_tables()
        if not MemorySafety.world_model_confidence_ok(confidence):
            confidence = settings.world_model_confidence_threshold

        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, mention_count FROM entities WHERE lower(name) = lower(?) LIMIT 1",
            (name,),
        )
        row = await cursor.fetchone()

        if row:
            entity_id = row["id"]
            await db.execute(
                """UPDATE entities SET mention_count = mention_count + 1,
                   last_seen_at = ?, confidence = MAX(confidence, ?),
                   freshness_score = 1.0
                   WHERE id = ?""",
                (now, confidence, entity_id),
            )
        else:
            entity_id = f"ent-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO entities
                   (id, name, entity_type, description, confidence, freshness_score,
                    first_seen_at, last_seen_at, mention_count)
                   VALUES (?, ?, ?, ?, ?, 1.0, ?, ?, 1)""",
                (entity_id, name, entity_type, description[:500], confidence, now, now),
            )
            if emit_fn:
                await emit_fn(
                    execution_id,
                    "entity_discovered",
                    "intelligence",
                    f"Discovered entity: {name}",
                    entityId=entity_id,
                    entityName=name,
                    entityType=entity_type,
                )

        if execution_id:
            link_id = f"ee-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO execution_entities
                   (id, execution_id, entity_id, context_snippet, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (link_id, execution_id, entity_id, context_snippet[:300], now),
            )

        await db.commit()
        return entity_id

    @classmethod
    async def link_entities(
        cls,
        source_id: str,
        target_id: str,
        relation_type: str = "related_to",
        *,
        confidence: float = 0.5,
        evidence: str = "",
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> str | None:
        if not settings.enable_vector_memory or source_id == target_id:
            return None

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        rel_id = f"rel-{uuid.uuid4().hex[:12]}"
        db = await get_db()
        await db.execute(
            """INSERT INTO entity_relationships
               (id, source_id, target_id, relation_type, confidence, evidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rel_id, source_id, target_id, relation_type, confidence, evidence[:300], now, now),
        )
        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "relationship_linked",
                "intelligence",
                f"Linked entities ({relation_type})",
                relationshipId=rel_id,
                sourceId=source_id,
                targetId=target_id,
                relationType=relation_type,
            )
        return rel_id

    @classmethod
    async def extract_from_text(
        cls,
        text: str,
        execution_id: str = "",
        *,
        emit_fn: EmitFn | None = None,
    ) -> list[str]:
        names = cls.extract_entity_candidates(text)
        ids: list[str] = []
        for name in names:
            eid = await cls.upsert_entity(
                name,
                execution_id=execution_id,
                context_snippet=text[:200],
                emit_fn=emit_fn,
            )
            if eid:
                ids.append(eid)

        for i in range(len(ids) - 1):
            await cls.link_entities(
                ids[i],
                ids[i + 1],
                "co_occurs_with",
                execution_id=execution_id,
                emit_fn=emit_fn,
            )
        return ids

    @classmethod
    async def get_graph(cls, limit: int = 50) -> dict[str, Any]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, name, entity_type, confidence, mention_count, last_seen_at
               FROM entities ORDER BY mention_count DESC LIMIT ?""",
            (limit,),
        )
        entities = [
            {
                "id": r["id"],
                "name": r["name"],
                "entityType": r["entity_type"],
                "confidence": r["confidence"],
                "mentionCount": r["mention_count"],
                "lastSeenAt": r["last_seen_at"],
            }
            for r in await cursor.fetchall()
        ]

        entity_ids = [e["id"] for e in entities]
        relationships: list[dict[str, Any]] = []
        if entity_ids:
            placeholders = ",".join("?" * len(entity_ids))
            cursor = await db.execute(
                f"""SELECT id, source_id, target_id, relation_type, confidence
                    FROM entity_relationships
                    WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
                    LIMIT 100""",
                entity_ids + entity_ids,
            )
            relationships = [
                {
                    "id": r["id"],
                    "sourceId": r["source_id"],
                    "targetId": r["target_id"],
                    "relationType": r["relation_type"],
                    "confidence": r["confidence"],
                }
                for r in await cursor.fetchall()
            ]

        return {"entities": entities, "relationships": relationships}

    @classmethod
    async def apply_confidence_decay(cls) -> int:
        """Decay freshness for entities not recently seen."""
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """UPDATE entities SET freshness_score = MAX(0.1,
                   freshness_score * 0.99),
               confidence = MAX(0.1, confidence * 0.995)
               WHERE last_seen_at < datetime('now', '-7 days')"""
        )
        await db.commit()
        return cursor.rowcount
