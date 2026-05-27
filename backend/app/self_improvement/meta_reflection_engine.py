"""
Meta reflection engine — recursive execution reflection (bounded depth <= 2).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.capability_gap_detector import CapabilityGapDetector
from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.recursive_safety import RecursiveSafety
from app.self_improvement.runtime_self_analysis import RuntimeSelfAnalysis

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class MetaReflectionEngine:
    @classmethod
    async def reflect(
        cls,
        execution_id: str,
        status: str,
        objective: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn,
        *,
        depth: int = 0,
        perf_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not EvolutionGovernor.is_meta_reflection_enabled():
            return {"skipped": True, "reason": "meta_reflection_disabled"}

        safety = await RecursiveSafety.preflight(ctx_snapshot, depth=depth)
        if not safety["allowed"]:
            return {"skipped": True, "reason": "safety_blocked", "safety": safety}

        await emit_fn(
            execution_id,
            "meta_reflection_started",
            "self_improvement",
            f"Meta reflection started (depth={depth})",
            depth=depth,
        )

        ctx_snapshot = {**ctx_snapshot, "status": status}
        analysis = RuntimeSelfAnalysis.analyze(ctx_snapshot, perf_snapshot)
        diag = await RuntimeSelfAnalysis.get_diagnostics_overlay(execution_id)
        if diag:
            analysis["diagnostics"] = diag

        gaps = await CapabilityGapDetector.detect(execution_id, ctx_snapshot, analysis, emit_fn)
        failure_clusters = await cls._cluster_failures(execution_id, status, ctx_snapshot, emit_fn)
        insights = await cls._generate_insights(
            execution_id, status, objective, analysis, gaps, emit_fn
        )

        reflection_id = await cls._persist_reflection(execution_id, depth, {
            "analysis": analysis,
            "gaps": gaps,
            "failureClusters": failure_clusters,
            "insights": insights,
            "objective": objective[:200],
            "status": status,
        })

        meta_result: dict[str, Any] = {
            "reflectionId": reflection_id,
            "depth": depth,
            "analysis": analysis,
            "gaps": gaps,
            "failureClusters": failure_clusters,
            "insights": insights,
        }

        if depth < RecursiveSafety.max_reflection_depth() and insights:
            secondary = await cls.reflect(
                execution_id,
                status,
                objective,
                {**ctx_snapshot, "meta_reflection_parent": reflection_id},
                emit_fn,
                depth=depth + 1,
                perf_snapshot=perf_snapshot,
            )
            meta_result["secondaryReflection"] = secondary

        for weakness in analysis.get("weaknesses") or []:
            if weakness.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "runtime_weakness_discovered",
                    "self_improvement",
                    f"Runtime weakness: {weakness.get('type')}",
                    weakness=weakness,
                )

        return meta_result

    @classmethod
    async def _cluster_failures(
        cls,
        execution_id: str,
        status: str,
        ctx: dict[str, Any],
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        if status != "failed":
            return []

        error = str(ctx.get("last_error") or ctx.get("failure_reason") or "unknown_failure")
        pattern_key = error[:80].lower().replace(" ", "_")
        pattern_id = f"fp-{uuid.uuid4().hex[:12]}"
        cluster_id = f"fc-{uuid.uuid4().hex[:12]}"
        pattern_data = {
            "error": error[:500],
            "retryCount": ctx.get("global_retry_count", 0),
            "executionId": execution_id,
        }
        await MetaReasoningMemory.upsert_failure_pattern(
            pattern_id, pattern_key, pattern_data, cluster_id=cluster_id
        )
        cluster = {"clusterId": cluster_id, "patternKey": pattern_key, "patternData": pattern_data}
        await emit_fn(
            execution_id,
            "failure_cluster_discovered",
            "self_improvement",
            f"Failure cluster: {pattern_key}",
            cluster=cluster,
        )
        return [cluster]

    @classmethod
    async def _generate_insights(
        cls,
        execution_id: str,
        status: str,
        objective: str,
        analysis: dict[str, Any],
        gaps: list[dict[str, Any]],
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        from app.config.settings import settings

        insights: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        if analysis.get("retryStorm"):
            insight = {
                "type": "retry_storm_mitigation",
                "message": "Consider backoff tuning and failure classification before retry",
                "confidence": 0.82,
            }
            insights.append(insight)

        if analysis.get("healthDegraded"):
            insight = {
                "type": "runtime_health",
                "message": "Runtime health degraded — defer evolution until stable",
                "confidence": 0.88,
            }
            insights.append(insight)

        if gaps and status == "failed":
            insight = {
                "type": "capability_gap_remediation",
                "message": f"{len(gaps)} capability gap(s) identified for objective class",
                "confidence": 0.79,
                "gapCount": len(gaps),
            }
            insights.append(insight)

        persisted: list[dict[str, Any]] = []
        for ins in insights:
            if ins["confidence"] < settings.self_improvement_confidence_threshold:
                continue
            ins_id = f"ri-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "reasoning_insights",
                {
                    "id": ins_id,
                    "insight_type": ins["type"],
                    "insight_data": json.dumps(ins),
                    "confidence": ins["confidence"],
                    "source_execution_id": execution_id,
                    "created_at": now,
                },
            )
            persisted.append({**ins, "id": ins_id})
            await emit_fn(
                execution_id,
                "reasoning_insight_generated",
                "self_improvement",
                ins["message"],
                insightId=ins_id,
                insightType=ins["type"],
                confidence=ins["confidence"],
            )
        return persisted

    @classmethod
    async def _persist_reflection(
        cls, execution_id: str, depth: int, data: dict[str, Any]
    ) -> str:
        ref_id = f"mr-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        confidence = 0.75
        if data.get("analysis", {}).get("retryStorm"):
            confidence = 0.85
        await MetaReasoningMemory.insert_row(
            "meta_reflections",
            {
                "id": ref_id,
                "execution_id": execution_id,
                "depth": depth,
                "reflection_data": json.dumps(data),
                "confidence": confidence,
                "created_at": now,
            },
        )
        return ref_id

    @classmethod
    async def get_timeline(cls, limit: int = 30) -> dict[str, Any]:
        return {
            "reflections": await MetaReasoningMemory.query_recent("meta_reflections", limit=limit),
            "insights": await MetaReasoningMemory.query_recent("reasoning_insights", limit=limit),
            "failurePatterns": await MetaReasoningMemory.query_recent("runtime_failure_patterns", limit=limit),
            "gaps": await MetaReasoningMemory.query_recent("capability_gaps", limit=limit),
        }
