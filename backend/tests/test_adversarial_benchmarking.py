"""Tests for adversarial benchmarking and stress evolution."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.adversarial_benchmarking import AdversarialBenchmarking
from app.self_improvement.failure_simulator import FailureSimulator
from app.self_improvement.scenario_generator import ScenarioGenerator
from app.self_improvement.stress_evolution import StressEvolution


class TestFailureSimulator:
    def test_retry_storm_simulation(self):
        sim = FailureSimulator.simulate("retry_storm")
        assert sim["simulationType"] == "retry_storm"
        assert sim["injectedFailures"] >= 5

    def test_hallucination_stress(self):
        sim = FailureSimulator.simulate("hallucination_stress")
        assert sim["groundingRequired"] is True


class TestScenarioGenerator:
    def test_generates_scenarios(self):
        async def run():
            with patch(
                "app.self_improvement.scenario_generator.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await ScenarioGenerator.generate_from_reflection(
                    {"analysis": {"retryStorm": True}, "gaps": []}
                )

        scenarios = asyncio.run(run())
        assert len(scenarios) >= 1
        assert scenarios[0].get("recommendationOnly") is True


class TestStressEvolution:
    def test_resilience_scoring(self):
        async def run():
            with patch(
                "app.self_improvement.stress_evolution.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await StressEvolution.run_stress_profile(
                    {"id": "sg-1", "scenarioType": "retry_storm", "confidence": 0.8}
                )

        result = asyncio.run(run())
        assert 0.2 <= result["resilienceScore"] <= 1.0


class TestAdversarialBenchmarking:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_ADVERSARIAL_BENCHMARKING", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()

        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            AdversarialBenchmarking.run_adversarial_pass("e1", {"analysis": {}}, emit)
        )
        assert result.get("skipped") is True
