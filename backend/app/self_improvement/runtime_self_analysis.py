"""
Runtime self-analysis — bottleneck diagnosis, retry-storm, coordination inefficiency.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RuntimeSelfAnalysis:
    @classmethod
    def analyze(cls, ctx_snapshot: dict[str, Any], perf_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        perf = perf_snapshot or {}
        retries = int(ctx_snapshot.get("global_retry_count") or 0)
        reflections = ctx_snapshot.get("reflection_history") or []
        tool_calls = ctx_snapshot.get("tool_calls") or []
        health = ctx_snapshot.get("runtime_health") or {}

        bottlenecks: list[dict[str, Any]] = []
        weaknesses: list[dict[str, Any]] = []

        avg_latency = float(perf.get("avgActionLatencyMs") or perf.get("avgLatencyMs") or 0)
        if avg_latency > 5000:
            bottlenecks.append({
                "type": "action_latency",
                "severity": "warning",
                "valueMs": avg_latency,
                "confidence": 0.82,
            })

        if retries >= 5:
            bottlenecks.append({
                "type": "retry_storm",
                "severity": "critical" if retries >= 8 else "warning",
                "retryCount": retries,
                "confidence": min(0.95, 0.6 + retries * 0.04),
            })

        if len(reflections) > 8:
            weaknesses.append({
                "type": "reflection_overhead",
                "reflectionCount": len(reflections),
                "confidence": 0.78,
            })

        if len(tool_calls) == 0 and ctx_snapshot.get("status") == "failed":
            weaknesses.append({
                "type": "tool_grounding_absent",
                "confidence": 0.75,
            })

        queue_depth = int(perf.get("queueDepth") or health.get("queueDepth") or 0)
        if queue_depth > 10:
            bottlenecks.append({
                "type": "queue_saturation",
                "queueDepth": queue_depth,
                "confidence": 0.8,
            })

        coordination_score = cls._coordination_efficiency(ctx_snapshot)
        if coordination_score < 0.5:
            weaknesses.append({
                "type": "coordination_inefficiency",
                "score": coordination_score,
                "confidence": 0.77,
            })

        return {
            "bottlenecks": bottlenecks,
            "weaknesses": weaknesses,
            "retryStorm": retries >= 5,
            "healthDegraded": bool(health.get("degraded")),
            "coordinationScore": coordination_score,
        }

    @classmethod
    def _coordination_efficiency(cls, ctx: dict[str, Any]) -> float:
        agents = ctx.get("active_agents") or ctx.get("spawned_agents") or []
        parallel = int(ctx.get("parallel_branch_count") or 0)
        retries = int(ctx.get("global_retry_count") or 0)
        base = 0.7
        if len(agents) > 6:
            base -= 0.1
        if parallel > 0:
            base += 0.05
        base -= min(0.3, retries * 0.03)
        return max(0.1, min(1.0, base))

    @classmethod
    async def get_diagnostics_overlay(cls, execution_id: str) -> dict[str, Any]:
        try:
            from app.runtime.runtime_diagnostics import RuntimeDiagnostics

            diag = RuntimeDiagnostics(execution_id)
            return diag.snapshot()
        except Exception as exc:
            logger.debug("Runtime diagnostics overlay unavailable: %s", exc)
            return {}
