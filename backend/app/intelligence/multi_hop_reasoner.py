"""
Multi-hop reasoner — entity path discovery with relationship scoring.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.graph_reasoner import GraphReasoner
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.memory_safety import MemorySafety
from app.intelligence.semantic_retriever import SemanticRetriever

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class MultiHopReasoner:
    """Hybrid semantic + symbolic multi-hop reasoning."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await KnowledgeGraph.ensure_tables()

    @classmethod
    def _path_score(cls, hops: list[dict[str, Any]]) -> float:
        if not hops:
            return 0.0
        score = 1.0
        for h in hops:
            score *= float(h.get("score", 0.5))
        return score * (1.0 + 0.05 * len(hops))

    @classmethod
    async def find_paths_between(
        cls,
        start_id: str,
        end_id: str,
        *,
        max_hops: int | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        await cls.ensure_tables()
        max_hops = max_hops or MemorySafety.max_reasoning_hops()
        db = await get_db()

        queue: list[tuple[str, list[dict[str, Any]], set[str]]] = [(start_id, [], {start_id})]
        results: list[dict[str, Any]] = []

        while queue and len(results) < MemorySafety.reasoning_budget():
            node, path, seen = queue.pop(0)
            if len(path) >= max_hops:
                continue

            cursor = await db.execute(
                """SELECT target_id, edge_type, weight, evidence_weight
                   FROM knowledge_graph_edges WHERE source_id = ?""",
                (node,),
            )
            for edge in await cursor.fetchall():
                target = edge["target_id"]
                if target in seen:
                    continue
                step = {
                    "from": node,
                    "to": target,
                    "type": edge["edge_type"],
                    "score": round(
                        float(edge["weight"] or 0.5) * 0.6
                        + float(edge["evidence_weight"] or 0.5) * 0.4,
                        3,
                    ),
                }
                new_path = path + [step]
                new_seen = seen | {target}

                if target == end_id:
                    results.append({
                        "startId": start_id,
                        "endId": end_id,
                        "hops": new_path,
                        "score": round(cls._path_score(new_path), 3),
                    })
                elif len(new_path) < max_hops:
                    queue.append((target, new_path, new_seen))

        return sorted(results, key=lambda r: r["score"], reverse=True)

    @classmethod
    async def reason_for_objective(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        seeds = EntityMemory.extract_entity_candidates(objective)
        entity_ids: list[str] = []
        for name in seeds[:5]:
            eid = await EntityMemory.upsert_entity(name, execution_id=execution_id)
            if eid:
                entity_ids.append(eid)

        await KnowledgeGraph.sync_from_entity_graph(execution_id)
        graph_result = await GraphReasoner.traverse_from_entities(
            entity_ids,
            execution_id=execution_id,
            query=objective,
            emit_fn=emit_fn,
        )

        semantic = await SemanticRetriever.retrieve_for_objective(
            objective,
            execution_id=execution_id,
            limit=max(2, MemorySafety.retrieval_budget() // 2),
            emit_fn=emit_fn,
        )

        cross_links: list[dict[str, Any]] = []
        if len(entity_ids) >= 2:
            for i in range(min(3, len(entity_ids) - 1)):
                paths = await cls.find_paths_between(entity_ids[i], entity_ids[i + 1])
                cross_links.extend(paths[:2])

        return {
            "enabled": True,
            "seedEntities": seeds,
            "entityIds": entity_ids,
            "graphPaths": graph_result.get("paths", []),
            "crossEntityPaths": cross_links,
            "semanticHits": len(semantic),
            "hybridScore": round(
                (graph_result.get("paths") or [{}])[0].get("score", 0.3)
                if graph_result.get("paths")
                else 0.3,
                3,
            ),
        }
