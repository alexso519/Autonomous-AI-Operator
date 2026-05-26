"""
Retry-policy adaptation — integrates with RetryCoordinator signals.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.safety_limits import clamp_drift

EmitFn = Callable[..., Awaitable[None]]


class RetryOptimizer:
    @classmethod
    async def evolve_policy(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        retry_count = int(ctx_snapshot.get("global_retry_count") or 0)
        reflections = ctx_snapshot.get("reflection_history") or []

        max_retries = 3
        if retry_count > 4:
            max_retries = 2
        elif retry_count == 0 and len(reflections) <= 1:
            max_retries = 4

        backoff = clamp_drift(1.5, 1.5 + retry_count * 0.05, 0.15)
        profile = {
            "maxRetries": max_retries,
            "backoffFactor": round(backoff, 2),
            "retryReduction": retry_count > 3,
        }

        if not dry_run:
            pid = f"rp-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            await ImprovementMemoryStore.insert_row(
                "retry_profiles",
                {
                    "id": pid,
                    "profile_key": f"retry_{execution_id[:8]}",
                    "max_retries": max_retries,
                    "backoff_factor": backoff,
                    "profile_data": json.dumps(profile),
                    "effectiveness": 0.7 if retry_count <= 2 else 0.45,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        if emit_fn:
            await emit_fn(
                execution_id,
                "retry_policy_evolved",
                "self_improvement",
                f"Retry profile max_retries={max_retries}",
                profile=profile,
                dryRun=dry_run,
            )
        return {"profile": profile, "dryRun": dry_run}
