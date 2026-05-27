"""
Coordination evolution — delegation, hierarchy, emergent multi-agent optimization.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.self_improvement.collaboration_patterns import CollaborationPatterns
from app.self_improvement.delegation_optimizer import DelegationOptimizer
from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.hierarchy_evolution import HierarchyEvolution
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class CoordinationEvolution:
    @classmethod
    async def evolve(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        meta_reflection: dict[str, Any],
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not EvolutionGovernor.is_coordination_evolution_enabled():
            return {"skipped": True, "reason": "coordination_evolution_disabled"}

        analysis = meta_reflection.get("analysis") or {}

        delegation = await DelegationOptimizer.optimize(ctx_snapshot, analysis)
        hierarchy = await HierarchyEvolution.evolve(ctx_snapshot, delegation)
        patterns = await CollaborationPatterns.discover_and_rank(ctx_snapshot, analysis)

        await cls._integrate_runtime(execution_id, delegation, patterns)

        if delegation.get("confidence", 0) >= 0.75:
            await emit_fn(
                execution_id,
                "delegation_strategy_evolved",
                "self_improvement",
                f"Delegation strategy: {delegation.get('strategy')}",
                profileId=delegation.get("id"),
                strategy=delegation.get("strategy"),
            )

        if hierarchy.get("score", 0) >= 0.75:
            await emit_fn(
                execution_id,
                "hierarchy_optimized",
                "self_improvement",
                f"Hierarchy generation {hierarchy.get('generation')} optimized",
                hierarchyId=hierarchy.get("id"),
                generation=hierarchy.get("generation"),
            )

        for pattern in patterns[:3]:
            if pattern.get("rankScore", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "coordination_pattern_discovered",
                    "self_improvement",
                    f"Pattern: {pattern.get('name')}",
                    patternId=pattern.get("id"),
                    patternName=pattern.get("name"),
                )

        if patterns:
            top = patterns[0]
            await emit_fn(
                execution_id,
                "collaboration_pattern_ranked",
                "self_improvement",
                f"Top pattern: {top.get('name')} (score {top.get('rankScore', 0):.2f})",
                topPattern=top.get("name"),
                rankScore=top.get("rankScore"),
            )

        return {
            "delegation": delegation,
            "hierarchy": hierarchy,
            "patterns": patterns,
        }

    @classmethod
    async def _integrate_runtime(
        cls,
        execution_id: str,
        delegation: dict[str, Any],
        patterns: list[dict[str, Any]],
    ) -> None:
        try:
            from app.execution.adaptive_graph import AdaptiveExecutionGraph

            _ = AdaptiveExecutionGraph
        except Exception:
            pass

        try:
            from app.cognition.collaboration_protocol import CollaborationProtocol

            _ = CollaborationProtocol
        except Exception:
            pass

        try:
            from app.cognition.consensus_manager import ConsensusManager

            _ = ConsensusManager
        except Exception:
            pass

        try:
            from app.execution.dynamic_planner import DynamicPlanner

            _ = DynamicPlanner
        except Exception:
            pass

        logger.debug(
            "Coordination evolution for %s: strategy=%s top_pattern=%s",
            execution_id,
            delegation.get("strategy"),
            patterns[0].get("name") if patterns else None,
        )

    @classmethod
    async def get_evolution_graph(cls, limit: int = 30) -> dict[str, Any]:
        return {
            "patterns": await MetaReasoningMemory.query_recent("coordination_patterns", limit=limit),
            "delegation": await MetaReasoningMemory.query_recent("delegation_profiles", limit=limit),
            "hierarchies": await MetaReasoningMemory.query_recent("hierarchy_generations", limit=limit),
            "rankings": await MetaReasoningMemory.query_recent("collaboration_rankings", limit=limit),
        }
