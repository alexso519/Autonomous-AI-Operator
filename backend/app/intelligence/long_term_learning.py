"""
Long-term learning — orchestrates cross-session learning after executions.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.knowledge_indexer import KnowledgeIndexer
from app.intelligence.pattern_generalizer import PatternGeneralizer
from app.intelligence.skill_accumulator import SkillAccumulator
from app.intelligence.strategy_evolution import StrategyEvolution
from app.intelligence.vector_memory import VectorMemory
from app.intelligence.world_model import WorldModel

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class LongTermLearning:
    """Post-execution learning pipeline."""

    @classmethod
    async def ensure_tables(cls) -> None:
        await VectorMemory.ensure_tables()
        await WorldModel.ensure_tables()
        await StrategyEvolution.ensure_tables()
        await SkillAccumulator.ensure_tables()
        await PatternGeneralizer.ensure_tables()

    @classmethod
    async def learn_from_execution(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        workflow_memory: dict[str, Any],
        *,
        duration: float = 0,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        await cls.ensure_tables()
        success = status == "completed"
        quality = float(workflow_memory.get("final_quality_score") or 0.5)

        indexed = await KnowledgeIndexer.index_from_workflow_memory(
            execution_id, workflow_memory, emit_fn=emit_fn
        )

        strategy = workflow_memory.get("selected_strategy") or {}
        strategy_id = ""
        if strategy.get("label"):
            strategy_id = await StrategyEvolution.record_outcome(
                strategy["label"],
                objective,
                success=success,
                quality=quality,
                duration=duration,
                approach=strategy.get("approach", ""),
            )
            if emit_fn and strategy_id:
                await emit_fn(
                    execution_id,
                    "strategy_learned",
                    "intelligence",
                    f"Learned strategy: {strategy['label']}",
                    strategyId=strategy_id,
                    strategyLabel=strategy["label"],
                    success=success,
                )

        patterns = await PatternGeneralizer.extract_from_execution(
            execution_id,
            objective,
            workflow_memory,
            status=status,
            emit_fn=emit_fn,
        )

        outputs = workflow_memory.get("outputs") or {}
        if isinstance(outputs, dict):
            for _nid, entry in list(outputs.items())[-5:]:
                agent = entry.get("agent_name", "Agent")
                output = entry.get("output", "")
                if output:
                    await SkillAccumulator.record_agent_outcome(
                        agent, success=success, quality=quality
                    )
                    await WorldModel.ingest_execution_text(
                        output, execution_id=execution_id, emit_fn=emit_fn
                    )

        tool_calls = workflow_memory.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if call.get("status") == "completed":
                    await SkillAccumulator.record_tool_outcome(
                        call.get("tool_name", "unknown"),
                        success=True,
                        quality=0.7,
                    )

        synthesis = workflow_memory.get("synthesized_output") or workflow_memory.get("final_output") or ""
        if synthesis:
            await WorldModel.ingest_execution_text(
                str(synthesis)[:3000],
                execution_id=execution_id,
                emit_fn=emit_fn,
            )

        return {
            "enabled": True,
            "indexedMemories": len(indexed),
            "strategyId": strategy_id,
            "patterns": patterns,
        }

    @classmethod
    async def get_planning_hints(cls, objective: str) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {}

        strategies = await StrategyEvolution.get_best_strategies(objective)
        patterns = await PatternGeneralizer.get_patterns_for_objective(objective)
        world_context = await WorldModel.get_context_for_objective(objective)

        return {
            "learnedStrategies": strategies,
            "executionPatterns": patterns,
            "worldModelContext": world_context,
        }
