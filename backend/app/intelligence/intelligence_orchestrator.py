"""
Intelligence orchestrator — runtime integration facade for the persistent intelligence layer.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.execution.context_manager import ContextManager
from app.intelligence.long_term_learning import LongTermLearning
from app.intelligence.semantic_retriever import SemanticRetriever
from app.intelligence.world_model import WorldModel

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class IntelligenceOrchestrator:
    """
    Hooks for pre/post execution intelligence integration.

    Called from CognitiveOrchestrator and MemoryRuntime without
    modifying execute_workflow() signature.
    """

    @classmethod
    async def run_pre_execution(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory or not objective.strip():
            return {"enabled": False}

        await LongTermLearning.ensure_tables()

        knowledge_reasoning: dict[str, Any] = {}
        try:
            from app.intelligence.knowledge_reasoning_coordinator import (
                KnowledgeReasoningCoordinator,
            )

            knowledge_reasoning = await KnowledgeReasoningCoordinator.run_pre_execution(
                execution_id, objective, emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("knowledge_reasoning", knowledge_reasoning)
        except Exception as exc:
            logger.warning("Knowledge pre-reasoning failed: %s", exc)

        memories = await SemanticRetriever.retrieve_for_objective(
            objective,
            execution_id=execution_id,
            emit_fn=emit_fn,
        )

        hints = await LongTermLearning.get_planning_hints(objective)
        world_block = hints.get("worldModelContext") or ""
        semantic_block = SemanticRetriever.format_context_block(memories)

        kr_block = ""
        if knowledge_reasoning.get("planning"):
            kr_block = knowledge_reasoning["planning"].get("contextBlock") or ""

        blocks = [b for b in (semantic_block, world_block, kr_block) if b]
        if blocks:
            ctx.set_workflow_memory("intelligence_context", {
                "semanticMemories": memories,
                "planningHints": hints,
                "knowledgeReasoning": knowledge_reasoning,
            })
            combined = "\n\n".join(blocks)
            ctx.set_context((ctx.get_context() or "") + f"\n\n{combined}")

        return {
            "enabled": True,
            "semanticMemories": memories,
            "planningHints": hints,
            "knowledgeReasoning": knowledge_reasoning,
        }

    @classmethod
    async def run_post_execution(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        duration: float = 0,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        shared = ctx.get_state()
        workflow = shared.get("workflow", shared)

        result = await LongTermLearning.learn_from_execution(
            execution_id,
            objective,
            status,
            workflow if isinstance(workflow, dict) else {},
            duration=duration,
            emit_fn=emit_fn,
        )

        from app.intelligence.memory_safety import MemorySafety

        compaction = await MemorySafety.run_compaction_job()
        maintenance = await WorldModel.maintenance()
        result["compaction"] = compaction
        result["maintenance"] = maintenance

        output_text = ""
        if isinstance(workflow, dict):
            output_text = str(workflow.get("final_output") or workflow.get("summary") or "")

        try:
            from app.intelligence.knowledge_reasoning_coordinator import (
                KnowledgeReasoningCoordinator,
            )

            result["knowledgeReasoning"] = await KnowledgeReasoningCoordinator.run_post_execution(
                execution_id,
                objective,
                output_text,
                status=status,
                emit_fn=emit_fn,
            )
        except Exception as exc:
            logger.warning("Knowledge post-reasoning failed: %s", exc)

        ctx.set_workflow_memory("intelligence_learning", result)
        return result

    @classmethod
    async def get_intelligence_snapshot(cls, execution_id: str) -> dict[str, Any]:
        from app.intelligence.vector_memory import VectorMemory
        from app.intelligence.strategy_evolution import StrategyEvolution
        from app.intelligence.skill_accumulator import SkillAccumulator

        memories = await VectorMemory.get_by_execution(execution_id)
        world = await WorldModel.get_snapshot()
        strategies = await StrategyEvolution.get_evolution_history(limit=10)
        agent_skills = await SkillAccumulator.get_top_skills("agent", limit=8)
        tool_skills = await SkillAccumulator.get_top_skills("tool", limit=8)

        knowledge_snapshot: dict[str, Any] = {}
        try:
            from app.intelligence.knowledge_reasoning_coordinator import (
                KnowledgeReasoningCoordinator,
            )

            knowledge_snapshot = await KnowledgeReasoningCoordinator.get_knowledge_snapshot()
        except Exception:
            pass

        return {
            "executionId": execution_id,
            "enabled": settings.enable_vector_memory,
            "semanticMemories": memories,
            "worldModel": world,
            "strategyEvolution": strategies,
            "agentSkills": agent_skills,
            "toolSkills": tool_skills,
            "knowledgeReasoning": knowledge_snapshot,
        }

    @classmethod
    async def retrieve_for_replanning(
        cls,
        execution_id: str,
        objective: str,
        failure_context: str,
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory:
            return []

        return await SemanticRetriever.retrieve_for_replanning(
            objective,
            failure_context,
            execution_id=execution_id,
            emit_fn=emit_fn,
        )
