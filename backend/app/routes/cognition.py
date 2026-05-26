"""
Cognition API — inspect deliberation, debate, and learning state.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.cognition.benchmark_analyzer import BenchmarkAnalyzer
from app.cognition.cognitive_orchestrator import CognitiveOrchestrator
from app.cognition.self_improvement_engine import SelfImprovementEngine
from app.database.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cognition", tags=["cognition"])


@router.get("/executions/{execution_id}")
async def get_execution_cognition(execution_id: str) -> dict:
    """Full cognition snapshot for an execution."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM executions WHERE id = ?",
        (execution_id,),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Execution not found")

    return await CognitiveOrchestrator.get_cognition_snapshot(execution_id)


@router.get("/insights/benchmarks")
async def get_benchmark_insights(days: int = 30) -> dict:
    """Benchmark history analysis for self-improvement dashboard."""
    return await BenchmarkAnalyzer.analyze_recent(days=days)


@router.get("/insights/improvements")
async def get_improvement_history(limit: int = 10) -> dict:
    """Recent self-improvement cycles."""
    cycles = await SelfImprovementEngine.get_recent_cycles(limit=limit)
    return {"cycles": cycles, "count": len(cycles)}
