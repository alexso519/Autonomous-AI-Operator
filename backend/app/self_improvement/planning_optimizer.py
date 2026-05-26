"""
Planning depth + reflection-threshold + context allocation optimization.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.safety_limits import clamp_drift

EmitFn = Callable[..., Awaitable[None]]


class PlanningOptimizer:
    @classmethod
    async def optimize(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        deliberation = ctx_snapshot.get("cognitive_deliberation") or {}
        uncertainty = float((deliberation.get("uncertainty") or {}).get("overall", 0.35))
        reflections = len(ctx_snapshot.get("reflection_history") or [])

        depth = 3 if uncertainty < 0.4 else 4 if uncertainty < 0.6 else 5
        reflection_threshold = clamp_drift(0.5, 0.45 + uncertainty * 0.2, 0.15)
        context_allocation = clamp_drift(0.5, 0.55 - reflections * 0.02, 0.15)

        profile = {
            "depth": depth,
            "reflectionThreshold": round(reflection_threshold, 3),
            "contextAllocation": round(context_allocation, 3),
            "hallucinationReduction": uncertainty > 0.55,
        }

        if not dry_run:
            profile_id = f"pp-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            await ImprovementMemoryStore.insert_row(
                "planning_profiles",
                {
                    "id": profile_id,
                    "profile_key": f"plan_{execution_id[:8]}",
                    "depth": depth,
                    "reflection_threshold": reflection_threshold,
                    "context_allocation": context_allocation,
                    "profile_data": json.dumps(profile),
                    "effectiveness": 0.6 + (0.1 if reflections < 3 else 0),
                    "created_at": now,
                    "updated_at": now,
                },
            )

        if emit_fn:
            await emit_fn(
                execution_id,
                "planning_profile_updated",
                "self_improvement",
                f"Planning profile depth={depth}",
                profile=profile,
                dryRun=dry_run,
            )
        return {"profile": profile, "dryRun": dry_run}
