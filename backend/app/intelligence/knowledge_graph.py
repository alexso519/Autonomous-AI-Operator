"""
Persistent knowledge graph — edges, neighborhoods, and graph storage.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class KnowledgeGraph:
    """Persistent graph layer atop entity_relationships with weighted edges."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await EntityMemory.ensure_tables()
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
                id              TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                target_id       TEXT NOT NULL,
                edge_type       TEXT NOT NULL DEFAULT 'related_to',
                weight          REAL NOT NULL DEFAULT 0.5,
                evidence_weight REAL NOT NULL DEFAULT 0.5,
                evidence        TEXT DEFAULT '',
                execution_id    TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS reasoning_paths (
                id              TEXT PRIMARY KEY,
                execution_id    TEXT DEFAULT '',
                start_entity_id TEXT NOT NULL,
                end_entity_id   TEXT NOT NULL,
                path_json       TEXT NOT NULL DEFAULT '[]',
                path_score      REAL NOT NULL DEFAULT 0.0,
                hop_count       INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS inferred_relationships (
                id              TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                target_id       TEXT NOT NULL,
                relation_type   TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                inference_method TEXT DEFAULT 'co_occurrence',
                evidence        TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS graph_reasoning_history (
                id              TEXT PRIMARY KEY,
                execution_id    TEXT DEFAULT '',
                query           TEXT DEFAULT '',
                result_summary  TEXT DEFAULT '',
                paths_found     INTEGER NOT NULL DEFAULT 0,
                depth_used      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON knowledge_graph_edges(source_id)"""
        )
        await db.commit()

    @classmethod
    async def upsert_edge(
        cls,
        source_id: str,
        target_id: str,
        edge_type: str = "related_to",
        *,
        weight: float = 0.5,
        evidence_weight: float = 0.5,
        evidence: str = "",
        execution_id: str = "",
    ) -> str | None:
        if not settings.enable_vector_memory or source_id == target_id:
            return None
        if not MemorySafety.world_model_confidence_ok(weight):
            weight = settings.world_model_confidence_threshold

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        edge_id = f"kge-{uuid.uuid4().hex[:12]}"
        db = await get_db()
        await db.execute(
            """INSERT INTO knowledge_graph_edges
               (id, source_id, target_id, edge_type, weight, evidence_weight,
                evidence, execution_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge_id, source_id, target_id, edge_type, weight, evidence_weight,
                evidence[:400], execution_id, now, now,
            ),
        )
        await db.commit()
        return edge_id

    @classmethod
    async def sync_from_entity_graph(cls, execution_id: str = "") -> int:
        """Mirror entity_relationships into knowledge_graph_edges."""
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, source_id, target_id, relation_type, confidence, evidence
               FROM entity_relationships ORDER BY updated_at DESC LIMIT 200"""
        )
        rows = await cursor.fetchall()
        count = 0
        for r in rows:
            eid = await cls.upsert_edge(
                r["source_id"],
                r["target_id"],
                r["relation_type"],
                weight=float(r["confidence"] or 0.5),
                evidence_weight=float(r["confidence"] or 0.5),
                evidence=r["evidence"] or "",
                execution_id=execution_id,
            )
            if eid:
                count += 1
        return count

    @classmethod
    async def get_neighbors(
        cls,
        entity_id: str,
        *,
        max_depth: int = 1,
        limit: int = 30,
    ) -> dict[str, Any]:
        await cls.ensure_tables()
        max_depth = min(max_depth, MemorySafety.graph_traversal_depth_limit())
        db = await get_db()
        visited: set[str] = {entity_id}
        frontier = [entity_id]
        edges: list[dict[str, Any]] = []
        entities: dict[str, dict[str, Any]] = {}

        for _ in range(max_depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            cursor = await db.execute(
                f"""SELECT id, source_id, target_id, edge_type, weight, evidence_weight
                    FROM knowledge_graph_edges
                    WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
                    LIMIT ?""",
                frontier + frontier + [limit],
            )
            next_frontier: list[str] = []
            for r in await cursor.fetchall():
                edges.append({
                    "id": r["id"],
                    "sourceId": r["source_id"],
                    "targetId": r["target_id"],
                    "edgeType": r["edge_type"],
                    "weight": r["weight"],
                    "evidenceWeight": r["evidence_weight"],
                })
                for nid in (r["source_id"], r["target_id"]):
                    if nid not in visited:
                        visited.add(nid)
                        next_frontier.append(nid)
            frontier = next_frontier[:10]

        if visited:
            ph = ",".join("?" * len(visited))
            cursor = await db.execute(
                f"""SELECT id, name, entity_type, confidence FROM entities WHERE id IN ({ph})""",
                list(visited),
            )
            for r in await cursor.fetchall():
                entities[r["id"]] = {
                    "id": r["id"],
                    "name": r["name"],
                    "entityType": r["entity_type"],
                    "confidence": r["confidence"],
                }

        return {
            "centerId": entity_id,
            "entities": list(entities.values()),
            "edges": edges,
            "depth": max_depth,
        }

    @classmethod
    async def record_reasoning_history(
        cls,
        execution_id: str,
        query: str,
        result_summary: str,
        paths_found: int,
        depth_used: int,
    ) -> str:
        await cls.ensure_tables()
        hid = f"grh-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            """INSERT INTO graph_reasoning_history
               (id, execution_id, query, result_summary, paths_found, depth_used, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hid, execution_id, query[:300], result_summary[:500], paths_found, depth_used, now),
        )
        await db.commit()
        return hid

    @classmethod
    async def get_snapshot(cls, limit: int = 40) -> dict[str, Any]:
        await cls.ensure_tables()
        base = await EntityMemory.get_graph(limit=limit)
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, source_id, target_id, edge_type, weight, evidence_weight
               FROM knowledge_graph_edges ORDER BY weight DESC LIMIT ?""",
            (limit,),
        )
        kg_edges = [
            {
                "id": r["id"],
                "sourceId": r["source_id"],
                "targetId": r["target_id"],
                "edgeType": r["edge_type"],
                "weight": r["weight"],
                "evidenceWeight": r["evidence_weight"],
            }
            for r in await cursor.fetchall()
        ]
        return {**base, "knowledgeEdges": kg_edges}
