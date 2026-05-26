"""
Runtime bottleneck optimization — adaptive heuristic weighting, learning updates.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore

EmitFn = Callable[..., Awaitable[None]]


class RuntimeOptimizer:
    @classmethod
    async def optimize_from_snapshot(
        cls,
        execution_id: str,
        perf_snapshot: dict[str, Any] | None,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        bottlenecks = (perf_snapshot or {}).get("bottlenecks") or []
        optimizations: list[dict[str, Any]] = []

        for bn in bottlenecks[:5]:
            stage = bn.get("stage", "unknown")
            duration = float(bn.get("durationSeconds", 0))
            opt = await cls._record_optimization(
                execution_id,
                optimization_type="bottleneck",
                target=stage,
                before={"durationSeconds": duration},
                after={"suggestedParallelism": min(4, int(duration / 30) + 1)},
                confidence=0.7 if duration > 60 else 0.55,
            )
            optimizations.append(opt)

        weights = cls._adaptive_weights(perf_snapshot)
        mem_id = f"mem-{uuid.uuid4().hex[:12]}"
        await ImprovementMemoryStore.upsert_memory(
            mem_id,
            "runtime_heuristic_weights",
            "weights",
            weights,
            confidence=weights.get("confidence", 0.6),
        )

        if emit_fn and optimizations:
            await emit_fn(
                execution_id,
                "optimization_applied",
                "self_improvement",
                f"Recorded {len(optimizations)} runtime optimization(s)",
                optimizations=optimizations,
            )
            await emit_fn(
                execution_id,
                "runtime_learning_updated",
                "self_improvement",
                "Runtime heuristic weights updated",
                weights=weights,
            )

        return {"optimizations": optimizations, "weights": weights}

    @classmethod
    async def _record_optimization(
        cls,
        execution_id: str,
        *,
        optimization_type: str,
        target: str,
        before: dict[str, Any],
        after: dict[str, Any],
        confidence: float,
    ) -> dict[str, Any]:
        opt_id = f"ro-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await ImprovementMemoryStore.insert_row(
            "runtime_optimizations",
            {
                "id": opt_id,
                "optimization_type": optimization_type,
                "target": target,
                "before_value": json.dumps(before),
                "after_value": json.dumps(after),
                "confidence": confidence,
                "applied": 0,
                "source_execution_id": execution_id,
                "created_at": now,
            },
        )
        return {
            "id": opt_id,
            "type": optimization_type,
            "target": target,
            "before": before,
            "after": after,
            "confidence": confidence,
        }

    @classmethod
    def _adaptive_weights(cls, perf_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        total = float((perf_snapshot or {}).get("totalDurationSeconds", 0))
        agent_share = 0.0
        for stage in (perf_snapshot or {}).get("stages") or []:
            if stage.get("stage") == "agent":
                agent_share = float(stage.get("durationSeconds", 0)) / max(total, 1)
        return {
            "agentWeight": round(min(1.0, 0.5 + agent_share * 0.3), 3),
            "synthesisWeight": round(max(0.2, 0.4 - agent_share * 0.1), 3),
            "confidence": 0.65 if total > 0 else 0.5,
        }
