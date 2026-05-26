"""
Execution efficiency evolution — quality/latency trends from runtime performance data.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore

EmitFn = Callable[..., Awaitable[None]]


class PerformanceEvolution:
    @classmethod
    async def record_from_snapshot(
        cls,
        perf_snapshot: dict[str, Any] | None,
        quality_score: float | None = None,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        total = float((perf_snapshot or {}).get("totalDurationSeconds", 0))
        latency_score = max(0.0, min(1.0, 1.0 - total / 3600))
        quality = float(quality_score or 0.5)
        combined = round((latency_score + quality) / 2, 3)

        evo_id = f"pe-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "totalDurationSeconds": total,
            "latencyScore": latency_score,
            "qualityScore": quality,
            "bottleneckCount": len((perf_snapshot or {}).get("bottlenecks") or []),
        }
        await ImprovementMemoryStore.insert_row(
            "performance_evolution",
            {
                "id": evo_id,
                "metric": "efficiency",
                "evolution_data": json.dumps(data),
                "score": combined,
                "created_at": now,
            },
        )
        result = {"id": evo_id, **data, "combinedScore": combined}
        if emit_fn and execution_id:
            await emit_fn(
                execution_id,
                "performance_profile_evolved",
                "self_improvement",
                f"Performance score {combined}",
                profile=result,
            )
        return result
