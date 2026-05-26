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
