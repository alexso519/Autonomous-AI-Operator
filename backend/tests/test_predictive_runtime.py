"""Tests for predictive runtime intelligence."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.failure_predictor import FailurePredictor
from app.self_improvement.predictive_runtime import PredictiveRuntime
from app.self_improvement.proactive_recovery import ProactiveRecovery
from app.self_improvement.runtime_forecasting import RuntimeForecasting


class TestFailurePredictor:
    def test_predicts_retry_cascade(self):
        async def run():
            with patch(
                "app.self_improvement.failure_predictor.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await FailurePredictor.predict(
                    "e1",
                    {"global_retry_count": 5},
                    {"retryStorm": True},
                )

        preds = asyncio.run(run())
        types = [p["predictionType"] for p in preds]
        assert "retry_cascade" in types or "execution_failure" in types


class TestRuntimeForecasting:
    def test_queue_saturation_forecast(self):
        async def run():
            return await RuntimeForecasting.forecast(
                {"runtime_health": {"queueDepth": 12}},
                {"queueDepth": 12},
            )

        forecast = asyncio.run(run())
        assert forecast["queueSaturationProbability"] > 0.5


class TestProactiveRecovery:
    def test_plans_for_high_probability(self):
        async def run():
            with patch(
                "app.self_improvement.proactive_recovery.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                preds = [
                    {
                        "predictionType": "retry_cascade",
                        "probability": 0.85,
                        "confidence": 0.85,
                    }
                ]
                forecast = {"queueSaturationProbability": 0.3}
                return await ProactiveRecovery.plan("e1", preds, forecast)

        plans = asyncio.run(run())
        assert len(plans) >= 1
        assert plans[0].get("recommendationOnly") is True


class TestPredictiveRuntime:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_PREDICTIVE_RUNTIME", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()

        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            PredictiveRuntime.analyze("e1", {}, {"analysis": {}}, emit)
        )
        assert result.get("skipped") is True
