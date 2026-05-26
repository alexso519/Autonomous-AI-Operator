"""
Knowledge planner — graph reasoning before strategy selection.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.concept_mapper import ConceptMapper
from app.intelligence.domain_abstraction import DomainAbstraction
from app.intelligence.multi_hop_reasoner import MultiHopReasoner
from app.intelligence.reasoning_context_builder import ReasoningContextBuilder

EmitFn = Callable[..., Awaitable[None]]


class KnowledgePlanner:
    """Planning hints grounded in knowledge graph and domain patterns."""

    @classmethod
    async def plan_with_knowledge(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        ctx_data: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        ctx_data = ctx_data or {}
        uncertainty = float(
            (ctx_data.get("uncertainty") or {}).get("score", 0.5)
            if isinstance(ctx_data.get("uncertainty"), dict)
            else 0.5
        )
        confidence = 1.0 - uncertainty

        graph = await MultiHopReasoner.reason_for_objective(
            objective, execution_id=execution_id, emit_fn=emit_fn,
        )
        domain = await DomainAbstraction.record_pattern(objective)
        context_block = await ReasoningContextBuilder.build_context_block(
            objective,
            execution_id=execution_id,
            confidence=confidence,
            uncertainty=uncertainty,
            emit_fn=emit_fn,
        )

        domain_patterns = await DomainAbstraction.get_patterns(
            domain.get("domain", "") if domain else "",
            limit=5,
        )

        recommended = cls._recommend_structure(graph, domain_patterns)

        return {
            "enabled": True,
            "graphReasoning": graph,
            "domain": domain,
            "domainPatterns": domain_patterns,
            "contextBlock": context_block,
            "recommendedStructure": recommended,
            "hybridScore": graph.get("hybridScore", 0.3),
        }

    @classmethod
    def _recommend_structure(
        cls,
        graph: dict[str, Any],
        patterns: list[dict[str, Any]],
    ) -> str:
        if graph.get("crossEntityPaths"):
            return "multi_entity_research"
        if patterns and patterns[0].get("successRate", 0) > 0.7:
            return "reuse_prior_pattern"
        if graph.get("semanticHits", 0) > 2:
            return "semantic_grounded"
        return "exploratory"
