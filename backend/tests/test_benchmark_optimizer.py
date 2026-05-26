"""Tests for benchmark optimizer and regression detector."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.benchmark_optimizer import BenchmarkOptimizer
from app.self_improvement.regression_detector import RegressionDetector


class TestBenchmarkOptimizer:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_BENCHMARK_OPTIMIZATION", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        result = asyncio.run(BenchmarkOptimizer.run_optimization_pass())
        assert result.get("skipped")

    def test_regression_detector_healthy(self):
        with patch(
            "app.self_improvement.regression_detector.EvolutionPolicy.get_active_policy",
            new_callable=AsyncMock,
            return_value={"benchmarkSafetyThreshold": 0.70},
        ):
            result = asyncio.run(RegressionDetector.check_and_record({"successRate": 85}))
        assert result["healthy"]

    def test_regression_detector_fires(self):
        events: list[str] = []

        async def emit(_eid, etype, _agent, _msg, **_kw):
            events.append(etype)

        with patch(
            "app.self_improvement.regression_detector.ImprovementMemoryStore.insert_row",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.regression_detector.EvolutionPolicy.get_active_policy",
            new_callable=AsyncMock,
            return_value={"benchmarkSafetyThreshold": 0.70},
        ):
            result = asyncio.run(
                RegressionDetector.check_and_record(
                    {"successRate": 0.5},
                    emit,
                    execution_id="sys",
                )
            )
        assert not result["healthy"]
        assert "benchmark_regression_detected" in events

    def test_benchmark_pass_mocked(self, monkeypatch):
        monkeypatch.setenv("ENABLE_BENCHMARK_OPTIMIZATION", "1")
        from app.config import settings as settings_mod

        monkeypatch.setattr(
            "app.self_improvement.benchmark_optimizer.settings",
            settings_mod.Settings(),
        )
        with patch(
            "app.self_improvement.improvement_memory.ImprovementMemoryStore.insert_row",
            new_callable=AsyncMock,
        ), patch.object(
            BenchmarkOptimizer,
            "_rank_strategies",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.benchmark.benchmark_runner.BenchmarkRunner.get_history",
            new_callable=AsyncMock,
            return_value=[{"success": True, "taskId": "t1"}],
        ):
            result = asyncio.run(BenchmarkOptimizer.run_optimization_pass("e1"))
        assert result.get("evolutionId")
