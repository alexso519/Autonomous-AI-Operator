"""Tests for meta reflection engine and runtime self-analysis."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.meta_reflection_engine import MetaReflectionEngine
from app.self_improvement.recursive_safety import RecursiveSafety
from app.self_improvement.runtime_self_analysis import RuntimeSelfAnalysis


class TestRuntimeSelfAnalysis:
    def test_detects_retry_storm(self):
        analysis = RuntimeSelfAnalysis.analyze({"global_retry_count": 6})
        assert analysis["retryStorm"] is True
        assert any(b["type"] == "retry_storm" for b in analysis["bottlenecks"])

    def test_coordination_score(self):
        score = RuntimeSelfAnalysis._coordination_efficiency({"global_retry_count": 0})
        assert 0.1 <= score <= 1.0


class TestMetaReflectionEngine:
    def test_reflect_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "0")
        monkeypatch.setenv("ENABLE_META_REFLECTION", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()

        async def emit(*_a, **_k):
            pass

        result = asyncio.run(
            MetaReflectionEngine.reflect("e1", "failed", "obj", {}, emit)
        )
        assert result.get("skipped") is True

    def test_reflect_emits_events(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "1")
        monkeypatch.setenv("ENABLE_META_REFLECTION", "1")
        monkeypatch.setenv("SELF_EVOLUTION_KILL_SWITCH", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        RecursiveSafety.reset_for_tests()

        events: list[str] = []

        async def emit(_eid, etype, _agent, _msg, **_kw):
            events.append(etype)

        ctx = {"global_retry_count": 6, "status": "failed", "reflection_history": [{}] * 3}
        with patch(
            "app.self_improvement.meta_reflection_engine.MetaReasoningMemory.insert_row",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.meta_reflection_engine.MetaReasoningMemory.upsert_failure_pattern",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.capability_gap_detector.MetaReasoningMemory.insert_row",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.meta_reflection_engine.MetaReasoningMemory.count_today",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = asyncio.run(
                MetaReflectionEngine.reflect("e1", "failed", "test objective", ctx, emit, depth=0)
            )

        assert "meta_reflection_started" in events
        assert result.get("reflectionId") or result.get("analysis")

    def test_bounded_reflection_depth(self):
        assert RecursiveSafety.max_reflection_depth() <= 2
