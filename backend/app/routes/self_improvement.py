"""
Self-improvement API — dashboard, replay, policy inspection.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.self_improvement.evolution_policy import EvolutionPolicy
from app.self_improvement.heuristic_evolution import HeuristicEvolution
from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.self_improvement_orchestrator import SelfImprovementOrchestrator

router = APIRouter(prefix="/api/self-improvement", tags=["self-improvement"])


@router.get("/dashboard")
async def get_dashboard(limit: int = 20) -> dict:
    """Aggregate self-improvement state for command center UI."""
    return await SelfImprovementOrchestrator.get_dashboard_snapshot(limit=limit)


@router.get("/sessions/{session_id}/replay")
async def replay_session(session_id: str) -> dict:
    """Replay an improvement session with audit trail."""
    data = await SelfImprovementOrchestrator.replay_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@router.get("/heuristics/lineage")
async def get_heuristic_lineage(limit: int = 30) -> dict:
    return await HeuristicEvolution.get_lineage(limit=limit)


@router.get("/policy")
async def get_policy() -> dict:
    return await EvolutionPolicy.get_active_policy()


@router.get("/audits")
async def get_audits(limit: int = 50) -> dict:
    await ImprovementMemoryStore.ensure_tables()
    audits = await ImprovementMemoryStore.query_recent("improvement_audits", limit=limit)
    return {"audits": audits, "count": len(audits)}


@router.get("/regressions")
async def get_regressions(limit: int = 20) -> dict:
    events = await ImprovementMemoryStore.query_recent("regression_events", limit=limit)
    return {"regressions": events, "count": len(events)}


@router.get("/phase2/dashboard")
async def get_phase2_dashboard(limit: int = 20) -> dict:
    """Recursive intelligence / Phase 2 evolution dashboard."""
    from app.self_improvement.recursive_intelligence_coordinator import (
        RecursiveIntelligenceCoordinator,
    )

    return await RecursiveIntelligenceCoordinator.get_phase2_dashboard(limit=limit)


@router.get("/meta-reflection/timeline")
async def get_meta_reflection_timeline(limit: int = 30) -> dict:
    from app.self_improvement.meta_reflection_engine import MetaReflectionEngine

    return await MetaReflectionEngine.get_timeline(limit=limit)


@router.get("/capabilities/explorer")
async def get_capability_explorer(limit: int = 30) -> dict:
    from app.self_improvement.capability_discovery import CapabilityDiscovery

    return await CapabilityDiscovery.get_explorer_data(limit=limit)


@router.get("/adversarial/replay")
async def get_adversarial_replay(limit: int = 20) -> dict:
    from app.self_improvement.adversarial_benchmarking import AdversarialBenchmarking

    return await AdversarialBenchmarking.get_replay_data(limit=limit)


@router.get("/coordination/evolution")
async def get_coordination_evolution(limit: int = 30) -> dict:
    from app.self_improvement.coordination_evolution import CoordinationEvolution

    return await CoordinationEvolution.get_evolution_graph(limit=limit)


@router.get("/predictive/dashboard")
async def get_predictive_dashboard(limit: int = 30) -> dict:
    from app.self_improvement.predictive_runtime import PredictiveRuntime

    return await PredictiveRuntime.get_dashboard(limit=limit)


@router.get("/governance")
async def get_evolution_governance() -> dict:
    from app.self_improvement.evolution_governor import EvolutionGovernor

    return await EvolutionGovernor.get_governance_snapshot()
