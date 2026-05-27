"""Tests for autonomous capability discovery."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.agent_architect import AgentArchitect
from app.self_improvement.autonomy_boundaries import AutonomyBoundaries
from app.self_improvement.capability_discovery import CapabilityDiscovery
from app.self_improvement.tool_gap_analyzer import ToolGapAnalyzer


class TestAutonomyBoundaries:
    def test_rejects_source_rewrite(self):
        ok, reason = AutonomyBoundaries.is_within_boundaries({"type": "source_rewrite"})
        assert not ok
        assert "forbidden" in reason

    def test_sanitize_proposal(self):
        prop = AutonomyBoundaries.sanitize_proposal({"type": "test", "confidence": 0.8})
        assert prop["recommendationOnly"] is True
        assert prop["modifiesSourceCode"] is False


class TestAgentArchitect:
    def test_proposes_recovery_on_retry_storm(self):
        async def run():
            with patch(
                "app.self_improvement.agent_architect.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await AgentArchitect.propose_archetypes(
                    {"global_retry_count": 5},
                    [],
                    {"retryStorm": True},
                )

        proposals = asyncio.run(run())
        names = [p["archetypeName"] for p in proposals]
        assert "recovery_agent" in names


class TestCapabilityDiscovery:
    def test_discover_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CAPABILITY_DISCOVERY", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()

        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            CapabilityDiscovery.discover("e1", {}, {"analysis": {}, "gaps": []}, emit)
        )
        assert result.get("skipped") is True

    def test_tool_gap_analyzer(self):
        async def run():
            with patch(
                "app.self_improvement.tool_gap_analyzer.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await ToolGapAnalyzer.analyze(
                    {"tool_calls": []},
                    [{"key": "hallucination_zone", "confidence": 0.8}],
                )

        reports = asyncio.run(run())
        assert len(reports) >= 1
        assert reports[0].get("recommendationOnly") is True
