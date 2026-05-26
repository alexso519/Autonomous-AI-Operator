"""
Resource pressure learning — queue/worker hints without mandatory distributed mode.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


class ResourceOptimizer:
    @classmethod
    async def optimize(
        cls,
        execution_id: str,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "queuePressure": "normal",
            "workerUtilization": 0.5,
            "recommendedConcurrency": 2,
        }

        try:
            from app.infrastructure.runtime_scaler import RuntimeScaler

            scaling = await RuntimeScaler.evaluate_scaling()
            profile["queuePressure"] = scaling.get("pressure", "normal")
            profile["recommendedConcurrency"] = scaling.get("recommendedConcurrency", 2)
            profile["workerUtilization"] = scaling.get("utilization", 0.5)
        except Exception:
            pass

        if emit_fn:
            await emit_fn(
                execution_id,
                "resource_profile_updated",
                "self_improvement",
                f"Resource profile: pressure={profile['queuePressure']}",
                profile=profile,
            )
        return profile
