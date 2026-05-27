"""
Runtime forecasting — queue saturation, workload, degraded-mode prediction.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RuntimeForecasting:
    @classmethod
    async def forecast(
        cls,
        ctx_snapshot: dict[str, Any],
        perf_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        perf = perf_snapshot or {}
        health = ctx_snapshot.get("runtime_health") or {}

        queue_depth = int(perf.get("queueDepth") or health.get("queueDepth") or 0)
        queue_saturation_prob = min(0.95, 0.3 + queue_depth * 0.05)

        workload = {
            "activeAgents": len(ctx_snapshot.get("active_agents") or []),
            "toolCalls": len(ctx_snapshot.get("tool_calls") or []),
            "retries": int(ctx_snapshot.get("global_retry_count") or 0),
        }
        workload_forecast = "stable"
        if workload["retries"] > 5:
            workload_forecast = "elevated"
        if queue_depth > 8:
            workload_forecast = "saturated"

        degraded_prob = 0.2
        if health.get("degraded"):
            degraded_prob = 0.85

        try:
            from app.infrastructure.runtime_scaler import RuntimeScaler

            _ = RuntimeScaler
        except Exception:
            pass

        return {
            "queueSaturationProbability": queue_saturation_prob,
            "queueDepth": queue_depth,
            "workloadForecast": workload_forecast,
            "workload": workload,
            "degradedModeProbability": degraded_prob,
            "recommendationOnly": True,
        }
