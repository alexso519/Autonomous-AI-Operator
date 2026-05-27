"""
Recursive intelligence coordinator — orchestrates Phase 2 bounded self-evolution.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.self_improvement.adversarial_benchmarking import AdversarialBenchmarking
from app.self_improvement.capability_discovery import CapabilityDiscovery
from app.self_improvement.coordination_evolution import CoordinationEvolution
from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.meta_reflection_engine import MetaReflectionEngine
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.predictive_runtime import PredictiveRuntime
from app.self_improvement.recursive_safety import RecursiveSafety

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class RecursiveIntelligenceCoordinator:
    """Bounded Phase 2 cycle — meta reflection through predictive runtime."""

    @classmethod
    def is_enabled(cls) -> bool:
        return (
            EvolutionGovernor.is_meta_reflection_enabled()
            or EvolutionGovernor.is_capability_discovery_enabled()
            or EvolutionGovernor.is_adversarial_enabled()
            or EvolutionGovernor.is_coordination_evolution_enabled()
            or EvolutionGovernor.is_predictive_runtime_enabled()
        )

    @classmethod
    async def run_phase2_cycle(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn,
        *,
        perf_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not cls.is_enabled():
            return {"skipped": True, "reason": "phase2_disabled"}

        if RecursiveSafety.is_kill_switch_active():
            return {"skipped": True, "reason": "kill_switch_active"}

        await MetaReasoningMemory.ensure_tables()
        safety = await RecursiveSafety.preflight(ctx_snapshot, depth=0)
        if not safety["allowed"]:
            return {"skipped": True, "reason": "safety_blocked", "safety": safety}

        results: dict[str, Any] = {"safety": safety}

        meta = await MetaReflectionEngine.reflect(
            execution_id, status, objective, ctx_snapshot, emit_fn,
            depth=0, perf_snapshot=perf_snapshot,
        )
        results["metaReflection"] = meta

        if meta.get("skipped"):
            return results

        discovery = await CapabilityDiscovery.discover(
            execution_id, ctx_snapshot, meta, emit_fn,
        )
        results["capabilityDiscovery"] = discovery

        adversarial = await AdversarialBenchmarking.run_adversarial_pass(
            execution_id, meta, emit_fn,
        )
        results["adversarial"] = adversarial

        coordination = await CoordinationEvolution.evolve(
            execution_id, ctx_snapshot, meta, emit_fn,
        )
        results["coordination"] = coordination

        predictive = await PredictiveRuntime.analyze(
            execution_id, ctx_snapshot, meta, emit_fn, perf_snapshot=perf_snapshot,
        )
        results["predictive"] = predictive

        results["governance"] = await EvolutionGovernor.get_governance_snapshot()
        results["inspectable"] = True
        results["replayable"] = True
        results["modifiesSourceCode"] = False

        return results

    @classmethod
    async def get_phase2_dashboard(cls, limit: int = 20) -> dict[str, Any]:
        await MetaReasoningMemory.ensure_tables()
        return {
            "metaReflection": await MetaReflectionEngine.get_timeline(limit=limit),
            "capabilityDiscovery": await CapabilityDiscovery.get_explorer_data(limit=limit),
            "adversarial": await AdversarialBenchmarking.get_replay_data(limit=limit),
            "coordination": await CoordinationEvolution.get_evolution_graph(limit=limit),
            "predictive": await PredictiveRuntime.get_dashboard(limit=limit),
            "governance": await EvolutionGovernor.get_governance_snapshot(),
        }
