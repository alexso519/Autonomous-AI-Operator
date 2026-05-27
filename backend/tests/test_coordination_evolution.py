"""Tests for coordination evolution."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.collaboration_patterns import CollaborationPatterns
from app.self_improvement.coordination_evolution import CoordinationEvolution
from app.self_improvement.delegation_optimizer import DelegationOptimizer


class TestDelegationOptimizer:
    def test_hierarchical_for_many_agents(self):
        async def run():
            with patch(
                "app.self_improvement.delegation_optimizer.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await DelegationOptimizer.optimize(
                    {"active_agents": [1, 2, 3, 4, 5]},
                    {"coordinationScore": 0.5},
                )

        profile = asyncio.run(run())
        assert profile["strategy"] == "hierarchical_delegation"


class TestCollaborationPatterns:
    def test_ranks_patterns(self):
        async def run():
            with patch(
                "app.self_improvement.collaboration_patterns.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await CollaborationPatterns.discover_and_rank(
                    {"parallel_branch_count": 2},
                    {"coordinationScore": 0.7},
                )

        ranked = asyncio.run(run())
        assert len(ranked) >= 1
        assert ranked[0]["rankScore"] >= ranked[-1]["rankScore"]


class TestCoordinationEvolution:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_COORDINATION_EVOLUTION", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()

        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            CoordinationEvolution.evolve("e1", {}, {"analysis": {}}, emit)
        )
        assert result.get("skipped") is True
