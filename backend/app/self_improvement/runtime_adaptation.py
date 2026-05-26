"""
Autonomous runtime adaptation — concurrency, timeouts, degraded mode (bounded).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from app.config.settings import settings

EmitFn = Callable[..., Awaitable[None]]


class RuntimeAdaptation:
    @classmethod
    async def adapt(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        perf_snapshot: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_runtime_adaptation:
            return {"skipped": True}

        adaptations: dict[str, Any] = {
            "concurrencyHint": cls._concurrency_hint(ctx_snapshot, perf_snapshot),
            "timeoutMultiplier": cls._timeout_multiplier(perf_snapshot),
            "degradedMode": cls._should_degrade(ctx_snapshot),
            "modelRoutingBias": "strong" if ctx_snapshot.get("cognitive_deliberation") else "light",
        }

        if emit_fn:
            await emit_fn(
                execution_id,
                "runtime_adapted",
                "self_improvement",
                "Runtime adaptation profile computed",
                adaptations=adaptations,
            )
        return adaptations

    @classmethod
    def _concurrency_hint(
        cls, ctx: dict[str, Any], perf: dict[str, Any] | None
    ) -> int:
        base = min(settings.max_worker_concurrency, 4)
        retries = int(ctx.get("global_retry_count") or 0)
        if retries > 5:
            return max(1, base - 2)
        bottlenecks = len((perf or {}).get("bottlenecks") or [])
        if bottlenecks > 3:
            return max(1, base - 1)
        return base

    @classmethod
    def _timeout_multiplier(cls, perf: dict[str, Any] | None) -> float:
        total = float((perf or {}).get("totalDurationSeconds", 0))
        if total > 1800:
            return 1.2
        if total < 120:
            return 0.9
        return 1.0

    @classmethod
    def _should_degrade(cls, ctx: dict[str, Any]) -> bool:
        return int(ctx.get("global_retry_count") or 0) > 6
