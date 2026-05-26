"""
Adaptive constraints — trigger bounded constraint adjustments during pressure.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


class AdaptiveConstraints:
    @classmethod
    async def evaluate(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        resource_profile: dict[str, Any],
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        triggered: list[str] = []
        constraints: dict[str, Any] = {
            "maxParallelAgents": 4,
            "reflectionCap": 8,
            "toolCallCap": 50,
        }

        if resource_profile.get("queuePressure") in ("high", "critical"):
            constraints["maxParallelAgents"] = 2
            triggered.append("queue_pressure")

        if int(ctx_snapshot.get("global_retry_count") or 0) > 5:
            constraints["reflectionCap"] = 5
            triggered.append("retry_pressure")

        if triggered and emit_fn:
            await emit_fn(
                execution_id,
                "adaptive_constraint_triggered",
                "self_improvement",
                f"Constraints adjusted: {', '.join(triggered)}",
                constraints=constraints,
                triggers=triggered,
            )

        return {"constraints": constraints, "triggered": triggered}
