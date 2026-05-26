"""
Knowledge reasoning coordinator — unified facade for Phase 2 knowledge system.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.causal_engine import CausalEngine
from app.intelligence.concept_mapper import ConceptMapper
from app.intelligence.conflict_resolver import ConflictResolver
from app.intelligence.domain_abstraction import DomainAbstraction
from app.intelligence.graph_reasoner import GraphReasoner
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.knowledge_planner import KnowledgePlanner
from app.intelligence.knowledge_synthesizer import KnowledgeSynthesizer
from app.intelligence.relationship_inference import RelationshipInference
from app.intelligence.research_synthesizer import ResearchSynthesizer
from app.intelligence.world_state_predictor import WorldStatePredictor

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class KnowledgeReasoningCoordinator:
    """Orchestrates graph, causal, synthesis, and cognitive retrieval pipelines."""

    @classmethod
    async def ensure_all_tables(cls) -> None:
        await KnowledgeGraph.ensure_tables()
        await CausalEngine.ensure_tables()
        await ConflictResolver.ensure_tables()
        from app.intelligence.belief_tracker import BeliefTracker
        from app.intelligence.evidence_reconciler import EvidenceReconciler

        await BeliefTracker.ensure_tables()
        await EvidenceReconciler.ensure_tables()
        await KnowledgeSynthesizer.ensure_tables()
        await DomainAbstraction.ensure_tables()
        await ConceptMapper.ensure_tables()

    @classmethod
    async def run_pre_execution(
        cls,
        execution_id: str,
        objective: str,
        *,
        uncertainty: float = 0.5,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory or not objective.strip():
            return {"enabled": False}

        await cls.ensure_all_tables()
        await KnowledgeGraph.sync_from_entity_graph(execution_id)

        planning = await KnowledgePlanner.plan_with_knowledge(
            objective, execution_id=execution_id, emit_fn=emit_fn,
        )
        prediction = await WorldStatePredictor.predict_from_objective(
            objective, execution_id=execution_id, emit_fn=emit_fn,
        )

        return {
            "enabled": True,
            "planning": planning,
            "prediction": prediction,
            "recommendedStructure": planning.get("recommendedStructure"),
            "contextBlock": planning.get("contextBlock", ""),
        }

    @classmethod
    async def run_post_execution(
        cls,
        execution_id: str,
        objective: str,
        output_text: str,
        *,
        status: str = "completed",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        await cls.ensure_all_tables()
        text = f"{objective}\n{output_text}"[:4000]

        graph = await GraphReasoner.reason_over_text(
            text, execution_id=execution_id, query=objective, emit_fn=emit_fn,
        )
        inferred = await RelationshipInference.infer_from_text(
            text, execution_id=execution_id, emit_fn=emit_fn,
        )
        await RelationshipInference.infer_transitive(limit=10)
        causal = await CausalEngine.infer_from_text(
            text, execution_id=execution_id, emit_fn=emit_fn,
        )
        concepts = await ConceptMapper.discover_concepts(
            text, execution_id=execution_id, emit_fn=emit_fn,
        )
        synthesis = await KnowledgeSynthesizer.synthesize_for_objective(
            objective, execution_id=execution_id, emit_fn=emit_fn,
        )
        domain = await DomainAbstraction.record_pattern(
            objective, success=status == "completed", execution_id=execution_id,
        )

        insight = None
        if synthesis.get("synthesis"):
            insight = await ConceptMapper.record_strategic_insight(
                domain.get("domain", "general") if domain else "general",
                synthesis["synthesis"].get("summary", "")[:200],
                execution_id=execution_id,
                emit_fn=emit_fn,
            )

        return {
            "enabled": True,
            "graphPaths": len(graph.get("paths", [])),
            "inferredRelations": len(inferred),
            "causalLinks": len(causal),
            "conceptsDiscovered": len(concepts),
            "synthesis": synthesis,
            "strategicInsight": insight,
            "domain": domain,
        }

    @classmethod
    async def get_knowledge_snapshot(cls) -> dict[str, Any]:
        from app.intelligence.belief_tracker import BeliefTracker
        from app.intelligence.multi_hop_reasoner import MultiHopReasoner

        await cls.ensure_all_tables()
        return {
            "enabled": settings.enable_vector_memory,
            "knowledgeGraph": await KnowledgeGraph.get_snapshot(),
            "inferredRelationships": await RelationshipInference.get_inferred(),
            "causalLinks": await CausalEngine.get_causal_history(limit=15),
            "openConflicts": await ConflictResolver.get_open_conflicts(),
            "beliefs": await BeliefTracker.get_beliefs(limit=15),
            "synthesized": await KnowledgeSynthesizer.get_synthesized(),
            "domainPatterns": await DomainAbstraction.get_patterns(limit=10),
            "strategicInsights": await ConceptMapper.get_insights(),
            "taxonomy": await ConceptMapper.generate_taxonomy_from_entities(),
        }

    @classmethod
    async def get_synthesized_for_debate(cls, topic: str, limit: int = 5) -> list[dict[str, Any]]:
        items = await KnowledgeSynthesizer.get_synthesized(limit=limit)
        topic_lower = topic.lower()
        return [
            s for s in items
            if topic_lower in s.get("topic", "").lower()
        ] or items[:limit]

    @classmethod
    async def query_causal_for_reflection(cls, subject: str = "") -> dict[str, Any]:
        links = await CausalEngine.get_causal_history(limit=20)
        if subject:
            links = [l for l in links if subject.lower() in str(l).lower()]
        validation = await CausalEngine.validate_temporal_consistency(subject) if subject else {}
        return {"causalLinks": links, "temporalValidation": validation}
