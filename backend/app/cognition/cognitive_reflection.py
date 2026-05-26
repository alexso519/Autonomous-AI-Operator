"""
Autonomous reflection cycles after execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.cognition.cognitive_memory_store import CognitiveMemoryStore
from app.cognition.self_improvement_engine import SelfImprovementEngine
from app.execution.context_manager import ContextManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class CognitiveReflection:
    """Post-execution analysis: failures, successes, reusable strategies."""

    @classmethod
    async def run_cycle(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        workflow = ctx.get_state().get("workflow", {})
        deliberation = workflow.get("cognitive_deliberation") or {}
        reflections = workflow.get("reflection_history") or []
        consensus = (deliberation.get("consensus") or {}).get("statement", "")
        best_branch = deliberation.get("bestBranch") or {}

        findings: list[str] = []
        stored_patterns: list[str] = []

        if status == "failed":
            failure_type = "execution_failed"
            if reflections:
                last = reflections[-1]
                issues = last.get("issues") or []
                if issues:
                    failure_type = issues[0]
            strategy = {
                "approach": best_branch.get("label", "retry_with_tools"),
                "retryHints": ["use_tools_for_facts", "expand_output"],
            }
            rec_id = await CognitiveMemoryStore.store_recovery_strategy(
                failure_type, strategy, execution_id
            )
            stored_patterns.append(rec_id)
            findings.append(f"Stored recovery strategy for {failure_type}")

        if status == "completed" and consensus:
            sem_id = await CognitiveMemoryStore.store_semantic(
                concept=objective[:120],
                knowledge=consensus[:2000],
                source_execution_id=execution_id,
            )
            stored_patterns.append(sem_id)

            wf_id = await CognitiveMemoryStore.store_workflow_pattern(
                pattern_name=best_branch.get("label", "default_execution"),
                pattern_data={
                    "plan": deliberation.get("plan"),
                    "uncertainty": deliberation.get("uncertainty"),
                    "branchScore": best_branch.get("score"),
                },
                execution_id=execution_id,
            )
            stored_patterns.append(wf_id)
            findings.append("Stored reusable workflow pattern")

        ep_id = await CognitiveMemoryStore.store_episodic(
            execution_id=execution_id,
            objective=objective,
            outcome=status,
            metadata={
                "consensus": consensus[:300] if consensus else None,
                "reflectionCount": len(reflections),
            },
        )
        stored_patterns.append(ep_id)

        improvement = await SelfImprovementEngine.run_improvement_cycle(
            execution_id=execution_id,
            status=status,
            objective=objective,
            ctx_snapshot=workflow,
        )

        for pattern_id in stored_patterns:
            await emit_fn(
                execution_id,
                "learning_pattern_stored",
                "cognition",
                f"Stored cognitive pattern {pattern_id[:16]}",
                patternId=pattern_id,
                memoryTypes=["episodic", "semantic", "workflow", "recovery"],
            )

        if improvement.get("strategyOptimizations"):
            await emit_fn(
                execution_id,
                "strategy_optimized",
                "cognition",
                f"Generated {len(improvement['strategyOptimizations'])} optimization(s)",
                optimizations=improvement["strategyOptimizations"],
                expectedGain=improvement.get("compositeExpectedGain"),
            )

        result = {
            "executionId": execution_id,
            "status": status,
            "findings": findings,
            "storedPatternIds": stored_patterns,
            "improvement": improvement,
            "heuristicUpdates": cls._evolve_heuristics(status, deliberation, reflections),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        }

        ctx.set_workflow_memory("cognitive_reflection", result)
        await ctx.persist()

        await emit_fn(
            execution_id,
            "cognitive_reflection_completed",
            "cognition",
            f"Reflection complete — {len(stored_patterns)} pattern(s) stored",
            reflection=result,
        )

        return result

    @classmethod
    def _evolve_heuristics(
        cls,
        status: str,
        deliberation: dict[str, Any],
        reflections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Bounded heuristic updates stored for future planning."""
        uncertainty = (deliberation.get("uncertainty") or {}).get("overall", 0.3)
        return {
            "preferDebateWhenUncertaintyAbove": 0.45 if status == "completed" else 0.4,
            "preferToolGrounding": len(reflections) > 0 or uncertainty > 0.5,
            "maxDeliberationBranches": 4,
            "note": "Heuristics are advisory only — no runtime self-modification",
        }
