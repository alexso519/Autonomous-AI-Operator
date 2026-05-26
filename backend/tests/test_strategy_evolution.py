"""Tests for strategy evolution and cross-session learning."""

import asyncio
import os
import sys
import tempfile
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.config.settings import settings
from app.intelligence.long_term_learning import LongTermLearning
from app.intelligence.pattern_generalizer import PatternGeneralizer
from app.intelligence.skill_accumulator import SkillAccumulator
from app.intelligence.strategy_evolution import StrategyEvolution


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(settings, "database_path", path)
    monkeypatch.setattr(settings, "enable_vector_memory", True)

    import app.database.database as db_mod

    db_mod._db = None

    async def setup():
        from app.database.database import get_db

        await get_db()
        await LongTermLearning.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestStrategyEvolution:

    def test_record_and_rank(self, temp_db):
        objective = "Research local LLM inference tools"

        async def run():
            await StrategyEvolution.record_outcome(
                "Tool-First Grounding",
                objective,
                success=True,
                quality=0.85,
                duration=60,
            )
            await StrategyEvolution.record_outcome(
                "Sequential Deep Dive",
                objective,
                success=False,
                quality=0.4,
            )
            return await StrategyEvolution.get_best_strategies(objective)

        best = asyncio.run(run())
        assert len(best) >= 1
        assert best[0]["strategyLabel"] == "Tool-First Grounding"


class TestSkillAccumulator:

    def test_agent_and_tool_profiles(self, temp_db):
        async def run():
            await SkillAccumulator.record_agent_outcome("Research Agent", success=True, quality=0.8)
            await SkillAccumulator.record_tool_outcome("web_search", success=True, quality=0.9)
            agents = await SkillAccumulator.get_top_skills("agent")
            tools = await SkillAccumulator.get_top_skills("tool")
            return agents, tools

        agents, tools = asyncio.run(run())
        assert any(a["key"] == "Research Agent" for a in agents)
        assert any(t["key"] == "web_search" for t in tools)


class TestPatternGeneralizer:

    def test_extract_patterns(self, temp_db):
        workflow = {
            "selected_strategy": {"label": "Tool-First Grounding", "approach": "tools first"},
            "tool_calls": [{"tool_name": "web_search", "status": "completed"}],
            "global_retry_count": 1,
        }

        async def run():
            return await PatternGeneralizer.extract_from_execution(
                "exec-1",
                "Research AI safety guidelines",
                workflow,
                status="completed",
            )

        patterns = asyncio.run(run())
        assert len(patterns) >= 2

    def test_heuristic_evolution_guard(self, temp_db):
        async def run():
            blocked = await PatternGeneralizer.record_heuristic_change(
                "quality_threshold", 0.5, 0.9, reason="too large jump"
            )
            ok = await PatternGeneralizer.record_heuristic_change(
                "quality_threshold", 0.5, 0.6, reason="incremental"
            )
            return blocked, ok

        blocked, ok = asyncio.run(run())
        assert blocked == ""
        assert ok != ""
