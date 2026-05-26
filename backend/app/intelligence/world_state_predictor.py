"""
World state predictor — predictive reasoning over knowledge graph state.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class WorldStatePredictor:
    """Predict likely world-state shifts from graph trends."""

    @classmethod
    async def predict_from_objective(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        seeds = EntityMemory.extract_entity_candidates(objective)
        predictions: list[dict[str, Any]] = []

        for name in seeds[:3]:
            db_entity = await cls._find_entity(name)
            if not db_entity:
                continue
            neighborhood = await KnowledgeGraph.get_neighbors(
                db_entity["id"],
                max_depth=min(2, MemorySafety.graph_traversal_depth_limit()),
            )
            for edge in neighborhood.get("edges", [])[:3]:
                predictions.append({
                    "entity": name,
                    "likelyRelation": edge.get("edgeType", "related_to"),
                    "targetId": edge.get("targetId"),
                    "confidence": round(float(edge.get("weight", 0.5)) * 0.9, 3),
                })

        state = {
            "enabled": True,
            "seedEntities": seeds,
            "predictions": predictions,
            "predictionCount": len(predictions),
        }

        if emit_fn and predictions:
            await emit_fn(
                execution_id,
                "predictive_world_state_generated",
                "intelligence",
                f"Generated {len(predictions)} world-state prediction(s)",
                predictionCount=len(predictions),
                seeds=seeds,
            )

        return state

    @classmethod
    async def _find_entity(cls, name: str) -> dict[str, Any] | None:
        from app.database.database import get_db

        await EntityMemory.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, name, confidence FROM entities WHERE lower(name) = lower(?) LIMIT 1",
            (name,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {"id": row["id"], "name": row["name"], "confidence": row["confidence"]}
