"""Tests for prompt/planning/retry/tool optimizers."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.planning_optimizer import PlanningOptimizer
from app.self_improvement.prompt_evolution import PromptEvolution
from app.self_improvement.retry_optimizer import RetryOptimizer
from app.self_improvement.tool_selection_optimizer import ToolSelectionOptimizer


class TestPromptEvolution:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_PROMPT_EVOLUTION", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        result = asyncio.run(
            PromptEvolution.evolve_from_execution("e1", "completed", {}, None, dry_run=True)
        )
        assert result.get("skipped")

    def test_planning_optimizer_dry_run(self):
        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            PlanningOptimizer.optimize(
                "e1",
                {"cognitive_deliberation": {"uncertainty": {"overall": 0.5}}},
                emit,
                dry_run=True,
            )
        )
        assert result["profile"]["depth"] >= 3

    def test_retry_optimizer_high_retries(self):
        result = asyncio.run(
            RetryOptimizer.evolve_policy("e1", {"global_retry_count": 5}, dry_run=True)
        )
        assert result["profile"]["maxRetries"] <= 3

    def test_tool_selection(self):
        result = asyncio.run(
            ToolSelectionOptimizer.optimize(
                "e1",
                {
                    "tool_calls": [
                        {"tool": "search", "status": "ok"},
                        {"tool": "bad", "error": "x"},
                    ]
                },
                dry_run=True,
            )
        )
        assert "search" in result["profile"]["preferredTools"]

    def test_prompt_evolve_when_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_PROMPT_EVOLUTION", "1")
        from app.config import settings as settings_mod

        new_settings = settings_mod.Settings()
        monkeypatch.setattr("app.self_improvement.prompt_evolution.settings", new_settings)
        with patch(
            "app.self_improvement.prompt_evolution.ImprovementMemoryStore.query_recent",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = asyncio.run(
                PromptEvolution.evolve_from_execution("e1", "completed", {}, dry_run=True)
            )
        assert result.get("dryRun") is True
