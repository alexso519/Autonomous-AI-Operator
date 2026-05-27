"""
Proactive recovery — predictive recovery planning and self-protection reasoning.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class ProactiveRecovery:
    @classmethod
    async def plan(
        cls,
        execution_id: str,
        predictions: list[dict[str, Any]],
        forecast: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        plans: list[dict[str, Any]] = []

        for pred in predictions:
            if pred.get("probability", 0) < 0.7:
                continue
            ptype = pred.get("predictionType", "")
            plan = cls._plan_for_prediction(ptype, pred, forecast)
            if not plan:
                continue
            plan_id = f"pr-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "recovery_plans",
                {
                    "id": plan_id,
                    "plan_type": plan["planType"],
                    "plan_data": json.dumps(plan),
                    "confidence": plan["confidence"],
                    "applied": 0,
                    "created_at": now,
                },
            )
            plans.append({**plan, "id": plan_id, "executionId": execution_id})

        try:
            from app.infrastructure.execution_recovery import ExecutionRecovery

            _ = ExecutionRecovery
        except Exception:
            pass

        return plans

    @classmethod
    def _plan_for_prediction(
        cls,
        ptype: str,
        pred: dict[str, Any],
        forecast: dict[str, Any],
    ) -> dict[str, Any] | None:
        plans_map = {
            "retry_cascade": {
                "planType": "backoff_and_circuit_break",
                "actions": ["increase_backoff", "enable_circuit_breaker", "classify_failure"],
                "confidence": 0.82,
            },
            "execution_failure": {
                "planType": "graceful_degradation",
                "actions": ["reduce_parallelism", "single_path_fallback"],
                "confidence": 0.8,
            },
            "degraded_mode": {
                "planType": "stabilize_runtime",
                "actions": ["pause_evolution", "reduce_load", "enable_recovery_mode"],
                "confidence": 0.85,
            },
            "timeout_risk": {
                "planType": "deadline_extension_or_abort",
                "actions": ["trim_reflections", "prioritize_critical_path"],
                "confidence": 0.76,
            },
            "hallucination_probability": {
                "planType": "grounding_enforcement",
                "actions": ["require_tool_call", "verify_citations"],
                "confidence": 0.78,
            },
        }
        plan = plans_map.get(ptype)
        if not plan:
            return None
        if forecast.get("queueSaturationProbability", 0) > 0.7:
            plan = {**plan, "actions": [*plan["actions"], "throttle_queue"]}
        return {**plan, "triggerPrediction": ptype, "recommendationOnly": True}

    @classmethod
    async def apply_adaptations(
        cls,
        execution_id: str,
        plans: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Record predictive adaptations (recommendation-first; applied=0 by default)."""
        now = datetime.now(timezone.utc).isoformat()
        adaptations: list[dict[str, Any]] = []

        for plan in plans:
            if plan.get("confidence", 0) < 0.75:
                continue
            adapt_id = f"pa-{uuid.uuid4().hex[:12]}"
            adaptation = {
                "adaptationType": plan.get("planType"),
                "actions": plan.get("actions"),
                "confidence": plan.get("confidence"),
                "executionId": execution_id,
                "recommendationOnly": True,
                "applied": False,
            }
            await MetaReasoningMemory.insert_row(
                "predictive_adaptations",
                {
                    "id": adapt_id,
                    "adaptation_type": adaptation["adaptationType"],
                    "adaptation_data": json.dumps(adaptation),
                    "confidence": adaptation["confidence"],
                    "applied": 0,
                    "created_at": now,
                },
            )
            adaptations.append({**adaptation, "id": adapt_id})

        return adaptations
