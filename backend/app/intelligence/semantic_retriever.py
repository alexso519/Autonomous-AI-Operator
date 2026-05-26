"""
Semantic retriever — context-aware ranking over vector memory.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.vector_memory import VectorMemory

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class SemanticRetriever:
    """Retrieve semantically relevant long-term memories with context ranking."""

    @classmethod
    async def retrieve_for_objective(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        memory_types: list[str] | None = None,
        limit: int = 5,
        context_tags: list[str] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory or not objective.strip():
            return []

        results = await VectorMemory.search(
            objective,
            memory_types=memory_types,
            limit=limit,
        )

        if context_tags:
            tagged = []
            for r in results:
                meta = r.get("metadata") or {}
                tags = meta.get("tags") or []
                boost = 0.05 * len(set(context_tags) & set(tags))
                r["combinedScore"] = round(r.get("combinedScore", 0) + boost, 4)
                tagged.append(r)
            tagged.sort(key=lambda x: x.get("combinedScore", 0), reverse=True)
            results = tagged[:limit]

        if emit_fn and results:
            await emit_fn(
                execution_id,
                "semantic_memory_retrieved",
                "intelligence",
                f"Retrieved {len(results)} semantic memory(ies)",
                count=len(results),
                topScore=results[0].get("combinedScore", 0),
                memoryIds=[r["id"] for r in results],
            )

        return results

    @classmethod
    async def retrieve_for_replanning(
        cls,
        objective: str,
        failure_context: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        query = f"{objective}\nFailure context: {failure_context[:500]}"
        return await cls.retrieve_for_objective(
            query,
            execution_id=execution_id,
            memory_types=["execution", "pattern", "strategy"],
            limit=3,
            emit_fn=emit_fn,
        )

    @classmethod
    def format_context_block(cls, memories: list[dict[str, Any]]) -> str:
        if not memories:
            return ""
        lines = ["[Long-term semantic memory]"]
        for m in memories:
            score = m.get("combinedScore", m.get("similarity", 0))
            content = (m.get("content") or "")[:400]
            lines.append(f"- ({m.get('memoryType', '?')}, score={score:.2f}) {content}")
        return "\n".join(lines)
