"""
Coordinates sub-optimizers across heuristic, prompt, benchmark, and runtime domains.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.self_improvement.adaptive_constraints import AdaptiveConstraints
from app.self_improvement.benchmark_optimizer import BenchmarkOptimizer
from app.self_improvement.capability_trends import CapabilityTrends
from app.self_improvement.execution_adaptation import ExecutionAdaptation
from app.self_improvement.heuristic_evolution import HeuristicEvolution
from app.self_improvement.improvement_safety import ImprovementSafety
from app.self_improvement.performance_evolution import PerformanceEvolution
from app.self_improvement.planning_optimizer import PlanningOptimizer
from app.self_improvement.prompt_evolution import PromptEvolution
from app.self_improvement.regression_detector import RegressionDetector
from app.self_improvement.resource_optimizer import ResourceOptimizer
from app.self_improvement.retry_optimizer import RetryOptimizer
from app.self_improvement.runtime_adaptation import RuntimeAdaptation
from app.self_improvement.runtime_optimizer import RuntimeOptimizer
from app.self_improvement.tool_selection_optimizer import ToolSelectionOptimizer

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class ImprovementCoordinator:
    @classmethod
    async def run_all_optimizers(
        cls,
        execution_id: str,
        status: str,
        objective: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn,
        *,
        dry_run: bool = True,
        perf_snapshot: dict[str, Any] | None = None,
        quality_score: float | None = None,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}

        results["heuristic"] = await HeuristicEvolution.evolve_from_execution(
            execution_id, status, objective, ctx_snapshot, emit_fn
        )

        results["runtime"] = await RuntimeOptimizer.optimize_from_snapshot(
            execution_id, perf_snapshot, emit_fn
        )

        if settings.enable_prompt_evolution:
            results["prompt"] = await PromptEvolution.evolve_from_execution(
                execution_id, status, ctx_snapshot, emit_fn, dry_run=dry_run
            )
            results["planning"] = await PlanningOptimizer.optimize(
                execution_id, ctx_snapshot, emit_fn, dry_run=dry_run
            )
            results["retry"] = await RetryOptimizer.evolve_policy(
                execution_id, ctx_snapshot, emit_fn, dry_run=dry_run
            )
            results["tools"] = await ToolSelectionOptimizer.optimize(
                execution_id, ctx_snapshot, emit_fn, dry_run=dry_run
            )

        if settings.enable_runtime_adaptation:
            results["runtimeAdaptation"] = await RuntimeAdaptation.adapt(
                execution_id, ctx_snapshot, perf_snapshot, emit_fn
            )
            results["executionAdaptation"] = await ExecutionAdaptation.adapt_reliability(
                execution_id, status, ctx_snapshot, emit_fn
            )
            resource = await ResourceOptimizer.optimize(execution_id, emit_fn)
            results["resource"] = resource
            results["constraints"] = await AdaptiveConstraints.evaluate(
                execution_id, ctx_snapshot, resource, emit_fn
            )

        if settings.enable_benchmark_optimization:
            bench = await BenchmarkOptimizer.run_optimization_pass(execution_id, emit_fn)
            results["benchmark"] = bench
            metrics = bench.get("summary", {})
            results["regression"] = await RegressionDetector.check_and_record(
                metrics, emit_fn, execution_id=execution_id
            )

        results["performance"] = await PerformanceEvolution.record_from_snapshot(
            perf_snapshot,
            quality_score,
            execution_id=execution_id,
            emit_fn=emit_fn,
        )
        results["capabilities"] = await CapabilityTrends.update_from_execution(
            execution_id, ctx_snapshot, emit_fn
        )

        proposed = cls._collect_proposed_actions(results)
        results["safety"] = await ImprovementSafety.preflight(ctx_snapshot, proposed)

        return results

    @classmethod
    def _collect_proposed_actions(cls, results: dict[str, Any]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for key in ("heuristic", "runtime", "prompt", "planning", "retry"):
            block = results.get(key)
            if not block or block.get("skipped"):
                continue
            actions.append(
                {
                    "type": f"optimize_{key}",
                    "confidence": 0.8 if key == "heuristic" else 0.7,
                    "modifiesSourceCode": False,
                    "detail": str(block)[:200],
                }
            )
        return actions
