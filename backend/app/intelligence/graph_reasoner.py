"""
Graph reasoner — evidence-weighted traversal with cycle detection.
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
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class GraphReasoner:
    """Symbolic + evidence-weighted graph traversal."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await KnowledgeGraph.ensure_tables()

    @classmethod
    def _score_edge(cls, weight: float, evidence_weight: float) -> float:
        return min(1.0, (weight * 0.6 + evidence_weight * 0.4))

    @classmethod
    async def traverse_from_entities(
        cls,
        entity_ids: list[str],
        *,
        execution_id: str = "",
        query: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory or not entity_ids:
            return {"paths": [], "neighborhood": {}}

        await cls.ensure_tables()
        max_depth = MemorySafety.graph_traversal_depth_limit()
        budget = MemorySafety.reasoning_budget()
        visited: set[str] = set()
        cycle_guard: set[tuple[str, str]] = set()
        scored_paths: list[dict[str, Any]] = []

        if emit_fn:
            await emit_fn(
                execution_id,
                "graph_reasoning_started",
                "intelligence",
                f"Graph reasoning over {len(entity_ids)} seed(s)",
                seedCount=len(entity_ids),
                maxDepth=max_depth,
            )

        db = await get_db()
        neighborhood: dict[str, Any] = {}
        for seed in entity_ids[:5]:
            neighborhood = await KnowledgeGraph.get_neighbors(seed, max_depth=max_depth)
            frontier = [(seed, [], 1.0)]
            hops = 0

            while frontier and hops < max_depth and len(scored_paths) < budget:
                node_id, path, path_score = frontier.pop(0)
                if MemorySafety.detect_traversal_cycle(visited, node_id):
                    continue
                visited.add(node_id)

                cursor = await db.execute(
                    """SELECT id, target_id, edge_type, weight, evidence_weight
                       FROM knowledge_graph_edges WHERE source_id = ? LIMIT 20""",
                    (node_id,),
                )
                for edge in await cursor.fetchall():
                    edge_key = (node_id, edge["target_id"])
                    if edge_key in cycle_guard:
                        continue
                    cycle_guard.add(edge_key)

                    step_score = cls._score_edge(
                        float(edge["weight"] or 0.5),
                        float(edge["evidence_weight"] or 0.5),
                    )
                    new_score = path_score * step_score
                    new_path = path + [{
                        "from": node_id,
                        "to": edge["target_id"],
                        "type": edge["edge_type"],
                        "score": round(step_score, 3),
                    }]

                    if len(new_path) >= 2 and new_score >= MemorySafety.min_inference_confidence():
                        path_id = f"rp-{uuid.uuid4().hex[:12]}"
                        now = datetime.now(timezone.utc).isoformat()
                        await db.execute(
                            """INSERT INTO reasoning_paths
                               (id, execution_id, start_entity_id, end_entity_id,
                                path_json, path_score, hop_count, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                path_id, execution_id, seed, edge["target_id"],
                                json.dumps(new_path),
                                new_score, len(new_path), now,
                            ),
                        )
                        scored_paths.append({
                            "pathId": path_id,
                            "startId": seed,
                            "endId": edge["target_id"],
                            "hops": len(new_path),
                            "score": round(new_score, 3),
                            "steps": new_path,
                        })
                        if emit_fn:
                            await emit_fn(
                                execution_id,
                                "reasoning_path_discovered",
                                "intelligence",
                                f"Path discovered ({len(new_path)} hops, score {new_score:.2f})",
                                pathId=path_id,
                                hopCount=len(new_path),
                                pathScore=new_score,
                            )

                    if edge["target_id"] not in visited and len(new_path) < max_depth:
                        frontier.append((edge["target_id"], new_path, new_score))
                hops += 1

        await db.commit()
        await KnowledgeGraph.record_reasoning_history(
            execution_id,
            query or "entity_traversal",
            f"Found {len(scored_paths)} paths",
            len(scored_paths),
            max_depth,
        )

        return {
            "paths": sorted(scored_paths, key=lambda p: p["score"], reverse=True)[:budget],
            "neighborhood": neighborhood if entity_ids else {},
            "visitedCount": len(visited),
        }

    @classmethod
    async def reason_over_text(
        cls,
        text: str,
        *,
        execution_id: str = "",
        query: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        entity_ids = await EntityMemory.extract_from_text(text, execution_id, emit_fn=emit_fn)
        await KnowledgeGraph.sync_from_entity_graph(execution_id)
        return await cls.traverse_from_entities(
            entity_ids,
            execution_id=execution_id,
            query=query or text[:120],
            emit_fn=emit_fn,
        )
