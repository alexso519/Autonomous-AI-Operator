"""
Failure predictor — execution failure and retry cascade prediction.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class FailurePredictor:
    @classmethod
    async def predict(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        predictions: list[dict[str, Any]] = []

        retries = int(ctx_snapshot.get("global_retry_count") or 0)
        if retries >= 3:
            prob = min(0.95, 0.5 + retries * 0.08)
            predictions.append(await cls._persist_prediction(
                execution_id, "retry_cascade", prob,
                {"retryCount": retries, "message": "Retry cascade likely without intervention"},
                now,
            ))

        if analysis.get("retryStorm"):
            predictions.append(await cls._persist_prediction(
                execution_id, "execution_failure", 0.85,
                {"reason": "retry_storm", "message": "High failure probability from retry storm"},
                now,
            ))

        if analysis.get("healthDegraded"):
            predictions.append(await cls._persist_prediction(
                execution_id, "degraded_mode", 0.78,
                {"reason": "runtime_degraded", "message": "Degraded mode likely to persist"},
                now,
            ))

        reflections = ctx_snapshot.get("reflection_history") or []
        if len(reflections) > 6:
            predictions.append(await cls._persist_prediction(
                execution_id, "timeout_risk", 0.72,
                {"reflectionCount": len(reflections), "message": "Timeout risk from reflection overhead"},
                now,
            ))

        tool_calls = ctx_snapshot.get("tool_calls") or []
        if not tool_calls and ctx_snapshot.get("status") != "completed":
            predictions.append(await cls._persist_prediction(
                execution_id, "hallucination_probability", 0.74,
                {"message": "Elevated hallucination probability without tool grounding"},
                now,
            ))

        for pred in predictions:
            await cls._persist_forecast(pred, now)

        return predictions

    @classmethod
    async def _persist_prediction(
        cls,
        execution_id: str,
        pred_type: str,
        probability: float,
        data: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        pred_id = f"rp-{uuid.uuid4().hex[:12]}"
        entry = {
            "id": pred_id,
            "predictionType": pred_type,
            "probability": probability,
            "data": data,
            "executionId": execution_id,
            "recommendationOnly": True,
        }
        await MetaReasoningMemory.insert_row(
            "runtime_predictions",
            {
                "id": pred_id,
                "prediction_type": pred_type,
                "prediction_data": json.dumps(entry),
                "probability": probability,
                "execution_id": execution_id,
                "created_at": now,
            },
        )
        return entry

    @classmethod
    async def _persist_forecast(cls, prediction: dict[str, Any], now: str) -> None:
        fc_id = f"ff-{uuid.uuid4().hex[:12]}"
        await MetaReasoningMemory.insert_row(
            "failure_forecasts",
            {
                "id": fc_id,
                "forecast_key": prediction["predictionType"],
                "forecast_data": json.dumps(prediction),
                "probability": prediction["probability"],
                "created_at": now,
            },
        )
