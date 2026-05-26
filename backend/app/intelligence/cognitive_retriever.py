"""
Cognitive retriever — cognition-aware retrieval with confidence-conditioned depth.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.memory_safety import MemorySafety
from app.intelligence.multi_hop_reasoner import MultiHopReasoner
from app.intelligence.semantic_retriever import SemanticRetriever

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class CognitiveRetriever:
    """Retrieval depth adapts to uncertainty and confidence."""

    @classmethod
    def _depth_for_confidence(cls, confidence: float, uncertainty: float) -> int:
        if uncertainty > 0.6:
            return min(MemorySafety.retrieval_budget(), 12)
        if confidence > 0.75:
            return max(2, MemorySafety.retrieval_budget() // 3)
        return MemorySafety.retrieval_budget()

    @classmethod
    async def retrieve_for_cognition(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        confidence: float = 0.5,
        uncertainty: float = 0.5,
        reflection_mode: bool = False,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False, "memories": [], "graphContext": {}}

        limit = cls._depth_for_confidence(confidence, uncertainty)
        if reflection_mode:
            limit = min(limit + 3, 15)

        memories = await SemanticRetriever.retrieve_for_objective(
            objective,
            execution_id=execution_id,
            limit=limit,
            emit_fn=emit_fn,
        )

        graph_ctx = {}
        if uncertainty > 0.45 or reflection_mode:
            graph_ctx = await MultiHopReasoner.reason_for_objective(
                objective,
                execution_id=execution_id,
                emit_fn=emit_fn,
            )

        return {
            "enabled": True,
            "memories": memories,
            "graphContext": graph_ctx,
            "retrievalDepth": limit,
            "reflectionMode": reflection_mode,
        }
