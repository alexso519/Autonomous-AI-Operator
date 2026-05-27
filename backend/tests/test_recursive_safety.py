"""Tests for recursive safety and evolution governance."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.autonomy_boundaries import AutonomyBoundaries
from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.recursive_safety import RecursiveSafety


class TestRecursiveSafety:
    def test_kill_switch_blocks(self, monkeypatch):
        monkeypatch.setenv("SELF_EVOLUTION_KILL_SWITCH", "1")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        RecursiveSafety.reset_for_tests()

        async def run():
            with patch(
                "app.self_improvement.recursive_safety.MetaReasoningMemory.count_today",
                new_callable=AsyncMock,
                return_value=0,
            ), patch(
                "app.self_improvement.recursive_safety.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await RecursiveSafety.preflight({"global_retry_count": 0})

        result = asyncio.run(run())
        assert result["allowed"] is False
        assert "kill_switch_active" in result["blockedReasons"]

    def test_rejects_source_rewrite_actions(self):
        async def run():
            RecursiveSafety.reset_for_tests()
            with patch(
                "app.self_improvement.recursive_safety.MetaReasoningMemory.count_today",
                new_callable=AsyncMock,
                return_value=0,
            ), patch(
                "app.self_improvement.recursive_safety.MetaReasoningMemory.insert_row",
                new_callable=AsyncMock,
            ):
                return await RecursiveSafety.preflight(
                    {"global_retry_count": 0},
                    proposed_actions=[
                        {"type": "source_rewrite", "confidence": 0.99, "modifiesSourceCode": True}
                    ],
                )

        result = asyncio.run(run())
        assert len(result["rejectedActions"]) >= 1

    def test_max_reflection_depth(self, monkeypatch):
        monkeypatch.setenv("MAX_RECURSIVE_REFLECTION_DEPTH", "2")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        assert RecursiveSafety.max_reflection_depth() == 2


class TestEvolutionGovernor:
    def test_meta_reflection_requires_both_flags(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "0")
        monkeypatch.setenv("ENABLE_META_REFLECTION", "1")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        assert EvolutionGovernor.is_meta_reflection_enabled() is False

        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "1")
        settings_mod.settings = settings_mod.Settings()
        assert EvolutionGovernor.is_meta_reflection_enabled() is True

    def test_coordination_complexity_cap(self):
        ok, reason = AutonomyBoundaries.check_coordination_complexity(15)
        assert not ok
        assert reason == "coordination_complexity_cap"
