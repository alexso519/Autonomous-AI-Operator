"""
Cognitive orchestrator — integrates deliberation, memory, and reflection with execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.cognition.cognitive_memory_store import CognitiveMemoryStore
from app.cognition.cognitive_reflection import CognitiveReflection
from app.cognition.deliberation_engine import DeliberationEngine
from app.execution.context_manager import ContextManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class CognitiveOrchestrator:
    """
    Bounded cognitive layer for the execution runtime.

    Hooks:
      - pre_execution: deliberation + memory retrieval
      - post_execution: reflection + self-improvement
    """

    @classmethod
    async def run_pre_execution(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not objective.strip():
            return {}

        await CognitiveMemoryStore.ensure_tables()

        # Skip if already deliberated (resume path)
        if ctx.get_workflow_memory("cognitive_deliberation"):
            return ctx.get_workflow_memory("cognitive_deliberation")

        hints: list[str] = []
        intelligence_result: dict[str, Any] = {}
        try:
            memories = await CognitiveMemoryStore.retrieve(objective, limit=3)
            for m in memories:
                content = m.get("content") or {}
                if m["memoryType"] == "workflow":
                    hints.append(str(content.get("name", "")))
                elif m["memoryType"] == "semantic":
                    hints.append(str(content.get("concept", ""))[:80])
                elif m["memoryType"] == "recovery":
                    hints.append(str(content.get("failureType", "")))
        except Exception as exc:
            logger.warning("Cognitive memory retrieval failed: %s", exc)

        try:
            from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator

            intelligence_result = await IntelligenceOrchestrator.run_pre_execution(
                execution_id=execution_id,
                objective=objective,
                ctx=ctx,
                emit_fn=emit_fn,
            )
            for s in intelligence_result.get("planningHints", {}).get("learnedStrategies", []):
                hints.append(s.get("strategyLabel", ""))
        except Exception as exc:
            logger.warning("Intelligence pre-execution failed: %s", exc)

        visual_context: dict[str, Any] = {}
        try:
            from app.computer_use.computer_use_orchestrator import ComputerUseOrchestrator
            from app.computer_use.computer_use_agents import is_desktop_objective

            if is_desktop_objective(objective):
                visual_context = await ComputerUseOrchestrator.inject_visual_context_into_cognition(
                    execution_id, ctx,
                )
                if visual_context:
                    ctx.set_workflow_memory("visual_context_pending", visual_context)
                    await emit_fn(
                        execution_id,
                        "log",
                        "VisualAnalyst",
                        "Visual context available for deliberation",
                        elementCount=len(
                            visual_context.get("visualMemory", {}).get("interactiveElements", [])
                        ),
                    )
        except Exception as exc:
            logger.debug("Visual context injection skipped: %s", exc)

        if hints:
            await emit_fn(
                execution_id,
                "log",
                "cognition",
                f"Loaded {len(hints)} prior cognitive pattern(s)",
                patterns=hints,
            )

        try:
            result = await DeliberationEngine.deliberate(
                execution_id=execution_id,
                objective=objective,
                ctx=ctx,
                emit_fn=emit_fn,
                memory_hints=hints,
            )
            await ctx.persist()

            # Inject deliberation guidance into workflow context
            if result.consensus:
                ctx.set_context(
                    (ctx.get_context() or "")
                    + f"\n\n[Cognitive consensus]\n{result.consensus.get('statement', '')[:800]}"
                )
            elif result.best_branch:
                ctx.set_context(
                    (ctx.get_context() or "")
                    + f"\n\n[Cognitive plan]\n{result.best_branch.get('reasoning', '')[:600]}"
                )

            out = result.to_dict()
            out["intelligence"] = intelligence_result
            return out
        except Exception as exc:
            logger.warning("Pre-execution deliberation failed: %s", exc)
            return {"error": str(exc), "intelligence": intelligence_result}

    @classmethod
    async def run_post_execution(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not objective.strip():
            return {}

        reflection_result: dict[str, Any] = {}
        try:
            reflection_result = await CognitiveReflection.run_cycle(
                execution_id=execution_id,
                objective=objective,
                status=status,
                ctx=ctx,
                emit_fn=emit_fn,
            )
        except Exception as exc:
            logger.warning("Post-execution cognitive reflection failed: %s", exc)
            reflection_result = {"error": str(exc)}

        try:
            from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator

            shared = ctx.get_state()
            workflow = shared.get("workflow", shared)
            duration = float((workflow if isinstance(workflow, dict) else {}).get("duration_seconds") or 0)
            learning = await IntelligenceOrchestrator.run_post_execution(
                execution_id=execution_id,
                objective=objective,
                status=status,
                ctx=ctx,
                emit_fn=emit_fn,
                duration=duration,
            )
            reflection_result["intelligenceLearning"] = learning
        except Exception as exc:
            logger.warning("Intelligence post-execution failed: %s", exc)

        return reflection_result

    @classmethod
    async def get_cognition_snapshot(cls, execution_id: str) -> dict[str, Any]:
        """API-facing snapshot for frontend cognition panels."""
        db_memories = await CognitiveMemoryStore.get_execution_memories(execution_id)

        from app.database.database import get_db

        db = await get_db()
        shared: dict[str, Any] = {}
        cursor = await db.execute(
            "SELECT shared_memory FROM executions WHERE id = ?",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if row and row["shared_memory"]:
            try:
                shared = json.loads(row["shared_memory"])
            except json.JSONDecodeError:
                shared = {}

        workflow = shared.get("workflow", shared)
        deliberation = workflow.get("cognitive_deliberation") or {}
        reflection = workflow.get("cognitive_reflection") or {}

        from app.cognition.strategy_optimizer import StrategyOptimizer

        optimization = await StrategyOptimizer.get_latest_for_execution(execution_id)

        intelligence: dict[str, Any] = {}
        try:
            from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator

            intelligence = await IntelligenceOrchestrator.get_intelligence_snapshot(execution_id)
        except Exception:
            pass

        return {
            "executionId": execution_id,
            "deliberation": deliberation,
            "reflection": reflection,
            "reasoningGraph": deliberation.get("reasoningGraph"),
            "hypotheses": deliberation.get("hypotheses", []),
            "debates": deliberation.get("debate"),
            "consensus": deliberation.get("consensus"),
            "uncertainty": deliberation.get("uncertainty"),
            "memories": db_memories,
            "optimization": optimization,
            "intelligence": intelligence,
        }
