"""
World model — unified facade over entities, facts, and temporal reasoning.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.fact_evolution import FactEvolution
from app.intelligence.timeline_reasoner import TimelineReasoner

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class WorldModel:
    """Persistent world model integrating entity graph and fact evolution."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await EntityMemory.ensure_tables()
        await FactEvolution.ensure_tables()

    @classmethod
    async def ingest_execution_text(
        cls,
        text: str,
        execution_id: str = "",
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory or not text.strip():
            return {"entities": [], "facts": []}

        entity_ids = await EntityMemory.extract_from_text(
            text, execution_id=execution_id, emit_fn=emit_fn
        )

        facts: list[dict[str, Any]] = []
        sentences = [s.strip() for s in text.replace("\n", ". ").split(".") if len(s.strip()) > 20]
        for sentence in sentences[:10]:
            if " is " in sentence.lower():
                parts = sentence.split(" is ", 1)
                if len(parts) == 2:
                    result = await FactEvolution.upsert_fact(
                        parts[0].strip()[:100],
                        parts[1].strip()[:300],
                        source_execution_id=execution_id,
                        emit_fn=emit_fn,
                    )
                    facts.append(result)

        return {"entities": entity_ids, "facts": facts}

    @classmethod
    async def get_context_for_objective(cls, objective: str) -> str:
        if not settings.enable_vector_memory:
            return ""

        blocks: list[str] = []
        entities = EntityMemory.extract_entity_candidates(objective)
        for ent in entities[:3]:
            block = await TimelineReasoner.temporal_context_block(ent)
            if block:
                blocks.append(block)

        return "\n\n".join(blocks)

    @classmethod
    async def get_snapshot(cls) -> dict[str, Any]:
        graph = await EntityMemory.get_graph(limit=30)
        timeline = await TimelineReasoner.get_knowledge_timeline(limit=15)
        return {
            "entityGraph": graph,
            "knowledgeTimeline": timeline,
            "enabled": settings.enable_vector_memory,
        }

    @classmethod
    async def maintenance(cls) -> dict[str, int]:
        entity_decay = await EntityMemory.apply_confidence_decay()
        fact_decay = await TimelineReasoner.compute_freshness_scores()
        return {"entitiesDecayed": entity_decay, "factsDecayed": fact_decay}
