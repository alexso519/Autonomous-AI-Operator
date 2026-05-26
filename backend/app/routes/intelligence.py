"""
Intelligence API — semantic memory, world model, and learning insights.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.database.database import get_db
from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator
from app.intelligence.timeline_reasoner import TimelineReasoner
from app.intelligence.world_model import WorldModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/executions/{execution_id}")
async def get_execution_intelligence(execution_id: str) -> dict:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM executions WHERE id = ?",
        (execution_id,),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Execution not found")
    return await IntelligenceOrchestrator.get_intelligence_snapshot(execution_id)


@router.get("/world-model")
async def get_world_model() -> dict:
    return await WorldModel.get_snapshot()


@router.get("/timeline")
async def get_knowledge_timeline(limit: int = 30, subject: str = "") -> dict:
    timeline = await TimelineReasoner.get_knowledge_timeline(
        limit=limit, subject_filter=subject
    )
    return {"timeline": timeline, "count": len(timeline)}


@router.get("/strategies")
async def get_strategy_evolution(limit: int = 20) -> dict:
    from app.intelligence.strategy_evolution import StrategyEvolution

    strategies = await StrategyEvolution.get_evolution_history(limit=limit)
    return {"strategies": strategies, "count": len(strategies)}


@router.get("/skills")
async def get_skill_profiles(limit: int = 10) -> dict:
    from app.intelligence.skill_accumulator import SkillAccumulator

    agents = await SkillAccumulator.get_top_skills("agent", limit=limit)
    tools = await SkillAccumulator.get_top_skills("tool", limit=limit)
    return {"agentSkills": agents, "toolSkills": tools}


@router.get("/knowledge-graph")
async def get_knowledge_graph(limit: int = 50) -> dict:
    from app.intelligence.knowledge_graph import KnowledgeGraph

    return await KnowledgeGraph.get_snapshot(limit=limit)


@router.get("/reasoning-paths")
async def get_reasoning_paths(limit: int = 30) -> dict:
    from app.database.database import get_db
    from app.intelligence.knowledge_graph import KnowledgeGraph

    await KnowledgeGraph.ensure_tables()
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, start_entity_id, end_entity_id, path_score, hop_count, created_at
           FROM reasoning_paths ORDER BY path_score DESC LIMIT ?""",
        (limit,),
    )
    paths = [
        {
            "id": r["id"],
            "startEntityId": r["start_entity_id"],
            "endEntityId": r["end_entity_id"],
            "pathScore": r["path_score"],
            "hopCount": r["hop_count"],
            "createdAt": r["created_at"],
        }
        for r in await cursor.fetchall()
    ]
    return {"paths": paths, "count": len(paths)}


@router.get("/causal-links")
async def get_causal_links(limit: int = 30) -> dict:
    from app.intelligence.causal_engine import CausalEngine

    links = await CausalEngine.get_causal_history(limit=limit)
    return {"links": links, "count": len(links)}


@router.get("/beliefs")
async def get_beliefs(subject: str = "", limit: int = 30) -> dict:
    from app.intelligence.belief_tracker import BeliefTracker

    beliefs = await BeliefTracker.get_beliefs(subject=subject, limit=limit)
    return {"beliefs": beliefs, "count": len(beliefs)}


@router.get("/synthesized")
async def get_synthesized_knowledge(limit: int = 20) -> dict:
    from app.intelligence.knowledge_synthesizer import KnowledgeSynthesizer

    items = await KnowledgeSynthesizer.get_synthesized(limit=limit)
    return {"synthesized": items, "count": len(items)}


@router.get("/strategic-insights")
async def get_strategic_insights(limit: int = 20) -> dict:
    from app.intelligence.concept_mapper import ConceptMapper

    insights = await ConceptMapper.get_insights(limit=limit)
    return {"insights": insights, "count": len(insights)}


@router.get("/conflicts")
async def get_knowledge_conflicts(limit: int = 20) -> dict:
    from app.intelligence.conflict_resolver import ConflictResolver

    conflicts = await ConflictResolver.get_open_conflicts(limit=limit)
    return {"conflicts": conflicts, "count": len(conflicts)}
