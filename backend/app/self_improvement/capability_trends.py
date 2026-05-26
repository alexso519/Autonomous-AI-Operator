"""
Capability evolution tracking — hallucination and quality trends.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore

EmitFn = Callable[..., Awaitable[None]]


class CapabilityTrends:
    @classmethod
    async def update_from_execution(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        reflections = ctx_snapshot.get("reflection_history") or []
        hallucination_risk = 0.3
        for r in reflections[-3:]:
            if r.get("hallucinationRisk"):
                hallucination_risk = max(hallucination_risk, float(r["hallucinationRisk"]))

        capabilities = [
            ("reasoning", 0.7 if ctx_snapshot.get("cognitive_deliberation") else 0.5),
            ("tool_usage", 0.75 if ctx_snapshot.get("tool_calls") else 0.4),
            ("hallucination_control", 1.0 - hallucination_risk),
        ]

        trends: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        for cap, score in capabilities:
            tid = f"ct-{uuid.uuid4().hex[:12]}"
            await ImprovementMemoryStore.insert_row(
                "capability_trends",
                {
                    "id": tid,
                    "capability": cap,
                    "trend_data": json.dumps({"executionId": execution_id, "score": score}),
                    "score": score,
                    "created_at": now,
                },
            )
            trends.append({"capability": cap, "score": score})

        if emit_fn:
            await emit_fn(
                execution_id,
                "capability_trend_updated",
                "self_improvement",
                f"Updated {len(trends)} capability trend(s)",
                trends=trends,
            )
        return {"trends": trends}
