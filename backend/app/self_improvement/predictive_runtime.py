"""
Predictive runtime intelligence — failure prediction, forecasting, proactive recovery.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.failure_predictor import FailurePredictor
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.proactive_recovery import ProactiveRecovery
from app.self_improvement.runtime_forecasting import RuntimeForecasting

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class PredictiveRuntime:
    @classmethod
    async def analyze(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        meta_reflection: dict[str, Any],
        emit_fn: EmitFn,
        *,
        perf_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not EvolutionGovernor.is_predictive_runtime_enabled():
            return {"skipped": True, "reason": "predictive_runtime_disabled"}

        analysis = meta_reflection.get("analysis") or {}

        predictions = await FailurePredictor.predict(execution_id, ctx_snapshot, analysis)
        forecast = await RuntimeForecasting.forecast(ctx_snapshot, perf_snapshot)
        plans = await ProactiveRecovery.plan(execution_id, predictions, forecast)
        adaptations = await ProactiveRecovery.apply_adaptations(execution_id, plans)

        await cls._integrate_diagnostics(execution_id)

        for pred in predictions:
            if pred.get("probability", 0) >= 0.7:
                await emit_fn(
                    execution_id,
                    "failure_predicted",
                    "self_improvement",
                    f"Failure predicted: {pred.get('predictionType')} ({pred.get('probability', 0):.0%})",
                    predictionId=pred.get("id"),
                    predictionType=pred.get("predictionType"),
                    probability=pred.get("probability"),
                )

        await emit_fn(
            execution_id,
            "runtime_forecast_generated",
            "self_improvement",
            f"Workload forecast: {forecast.get('workloadForecast')}",
            forecast=forecast,
        )

        for plan in plans:
            if plan.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "proactive_recovery_planned",
                    "self_improvement",
                    f"Recovery plan: {plan.get('planType')}",
                    planId=plan.get("id"),
                    planType=plan.get("planType"),
                )

        for adapt in adaptations:
            await emit_fn(
                execution_id,
                "predictive_adaptation_applied",
                "self_improvement",
                f"Predictive adaptation recorded: {adapt.get('adaptationType')}",
                adaptationId=adapt.get("id"),
                recommendationOnly=True,
            )

        return {
            "predictions": predictions,
            "forecast": forecast,
            "recoveryPlans": plans,
            "adaptations": adaptations,
        }

    @classmethod
    async def _integrate_diagnostics(cls, execution_id: str) -> None:
        try:
            from app.runtime.runtime_diagnostics import RuntimeDiagnostics

            diag = RuntimeDiagnostics(execution_id)
            _ = diag.snapshot()
        except Exception:
            pass

        try:
            from app.execution.execution_stability import ExecutionStabilityMonitor

            _ = ExecutionStabilityMonitor
        except Exception:
            pass

        try:
            from app.execution.retry_coordinator import RetryCoordinator

            _ = RetryCoordinator
        except Exception:
            pass

    @classmethod
    async def get_dashboard(cls, limit: int = 30) -> dict[str, Any]:
        return {
            "predictions": await MetaReasoningMemory.query_recent("runtime_predictions", limit=limit),
            "forecasts": await MetaReasoningMemory.query_recent("failure_forecasts", limit=limit),
            "recoveryPlans": await MetaReasoningMemory.query_recent("recovery_plans", limit=limit),
            "adaptations": await MetaReasoningMemory.query_recent("predictive_adaptations", limit=limit),
        }
