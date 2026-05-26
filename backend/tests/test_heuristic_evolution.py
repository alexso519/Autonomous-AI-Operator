"""Tests for heuristic evolution and strategy mutation."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.evolution_guardrails import EvolutionGuardrails
from app.self_improvement.heuristic_evolution import HeuristicEvolution
from app.self_improvement.strategy_mutator import StrategyMutator


class TestHeuristicEvolution:
    def test_mine_patterns(self):
        patterns = HeuristicEvolution._mine_patterns(
            {"reflection_history": [{}], "tool_calls": [{"tool": "search"}], "global_retry_count": 1},
            "completed",
        )
        assert patterns["toolCallCount"] == 1
        assert patterns["status"] == "completed"

    def test_extract_heuristics_completed(self):
        h = HeuristicEvolution._extract_successful_strategies(
            {"toolCallCount": 2, "retryCount": 0}, "completed"
        )
        assert h["orchestrationWeight"] > 0.5

    def test_evolve_from_execution(self):
        events: list[str] = []

        async def emit(_eid, etype, _agent, _msg, **_kw):
            events.append(etype)

        with patch(
            "app.self_improvement.heuristic_evolution.ImprovementMemoryStore.insert_row",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.heuristic_evolution.ImprovementMemoryStore.query_recent",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.self_improvement.strategy_mutator.ImprovementMemoryStore.insert_row",
            new_callable=AsyncMock,
        ):
            result = asyncio.run(
                HeuristicEvolution.evolve_from_execution(
                    "e1",
                    "completed",
                    "Test objective",
                    {"global_retry_count": 0, "tool_calls": [{"tool": "search"}]},
                    emit,
                )
            )
        assert result["generation"]["generation"] >= 1
        assert "heuristic_evolved" in events

    def test_strategy_mutator_scoring(self):
        score = StrategyMutator._score_fitness("tool_grounding", {"status": "completed", "usedTools": True})
        assert score > 0.6


class TestGuardrails:
    def test_stable_runtime(self):
        ok, _ = EvolutionGuardrails.check_runtime_stable({"global_retry_count": 1})
        assert ok

    def test_unstable_retries(self):
        ok, reason = EvolutionGuardrails.check_runtime_stable({"global_retry_count": 10})
        assert not ok
        assert reason == "excessive_retries"
