"""
Regression detection — benchmark safety threshold triggers rollback signal.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.evolution_policy import EvolutionPolicy
from app.self_improvement.improvement_memory import ImprovementMemoryStore

EmitFn = Callable[..., Awaitable[None]]


class RegressionDetector:
    @classmethod
    async def check_and_record(
        cls,
        current_metrics: dict[str, Any],
        emit_fn: EmitFn | None = None,
        *,
        execution_id: str = "",
    ) -> dict[str, Any]:
        policy = await EvolutionPolicy.get_active_policy()
        threshold = float(policy.get("benchmarkSafetyThreshold", 0.70))

        success_rate = float(current_metrics.get("successRate", current_metrics.get("overallSuccessRate", 100)))
        if success_rate > 1:
            success_rate = success_rate / 100.0

        regressions: list[dict[str, Any]] = []
        if success_rate < threshold:
            reg_id = f"reg-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            await ImprovementMemoryStore.insert_row(
                "regression_events",
                {
                    "id": reg_id,
                    "metric": "success_rate",
                    "before_value": threshold,
                    "after_value": success_rate,
                    "severity": "critical" if success_rate < threshold * 0.85 else "warning",
                    "event_data": json.dumps(current_metrics),
                    "created_at": now,
                },
            )
            regressions.append(
                {
                    "id": reg_id,
                    "metric": "success_rate",
                    "before": threshold,
                    "after": success_rate,
                }
            )
            if emit_fn:
                await emit_fn(
                    execution_id or "system",
                    "benchmark_regression_detected",
                    "self_improvement",
                    f"Success rate {success_rate:.2f} below threshold {threshold}",
                    regression=regressions[0],
                )

        return {"regressions": regressions, "healthy": len(regressions) == 0}
