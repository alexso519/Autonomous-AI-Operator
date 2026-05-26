"""Tests for runtime adaptation modules."""

import asyncio
import os
import sys
import tempfile
import uuid
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.self_improvement.adaptive_constraints import AdaptiveConstraints
from app.self_improvement.execution_adaptation import ExecutionAdaptation
from app.self_improvement.runtime_adaptation import RuntimeAdaptation


class TestRuntimeAdaptation:
    def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_RUNTIME_ADAPTATION", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        result = asyncio.run(RuntimeAdaptation.adapt("e1", {}))
        assert result.get("skipped")

    def test_adapt_when_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_RUNTIME_ADAPTATION", "1")
        from app.config import settings as settings_mod

        monkeypatch.setattr(
            "app.self_improvement.runtime_adaptation.settings",
            settings_mod.Settings(),
        )
        result = asyncio.run(RuntimeAdaptation.adapt("e1", {"global_retry_count": 0}))
        assert "concurrencyHint" in result

    def test_execution_adaptation(self):
        result = asyncio.run(
            ExecutionAdaptation.adapt_reliability("e1", "failed", {"global_retry_count": 6})
        )
        assert result["reliabilityScore"] < 0.5

    def test_adaptive_constraints_trigger(self):
        events: list[str] = []

        async def emit(eid, etype, agent, msg, **kw):
            events.append(etype)

        result = asyncio.run(
            AdaptiveConstraints.evaluate(
                "e1",
                {"global_retry_count": 8},
                {"queuePressure": "high"},
                emit,
            )
        )
        assert result["triggered"]
        assert "adaptive_constraint_triggered" in events
