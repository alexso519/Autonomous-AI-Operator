"""Tests for self-improvement orchestrator lifecycle."""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.execution.context_manager import ContextManager
from app.self_improvement.evolution_guardrails import EvolutionGuardrails
from app.self_improvement.improvement_safety import ImprovementSafety
from app.self_improvement.self_improvement_orchestrator import SelfImprovementOrchestrator


class TestSelfImprovementOrchestrator:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "0")
        from app.config import settings as settings_mod

        settings_mod.settings = settings_mod.Settings()
        assert not SelfImprovementOrchestrator.is_enabled()

    def test_full_cycle_dry_run(self, monkeypatch):
        monkeypatch.setenv("ENABLE_SELF_IMPROVEMENT", "1")
        monkeypatch.setenv("SELF_IMPROVEMENT_DRY_RUN", "1")
        from app.config import settings as settings_mod

        new_settings = settings_mod.Settings()
        monkeypatch.setattr(
            "app.self_improvement.self_improvement_orchestrator.settings",
            new_settings,
        )
        EvolutionGuardrails.reset_cooldown_for_tests()

        eid = "orch-test-1"
        ctx = ContextManager(execution_id=eid)
        ctx.set_workflow_memory("global_retry_count", 0)
        emit = AsyncMock()

        mock_conn = AsyncMock()
        with patch(
            "app.self_improvement.self_improvement_orchestrator.ImprovementMemoryStore.ensure_tables",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.self_improvement_orchestrator.ImprovementCoordinator.run_all_optimizers",
            new_callable=AsyncMock,
            return_value={
                "heuristic": {},
                "safety": {"allowed": True, "rejectedActions": []},
                "regression": {"healthy": True},
            },
        ), patch(
            "app.self_improvement.self_improvement_orchestrator.ImprovementMemoryStore.insert_row",
            new_callable=AsyncMock,
        ), patch(
            "app.self_improvement.self_improvement_orchestrator.ImprovementSafety.audit",
            new_callable=AsyncMock,
            return_value="aud-test",
        ), patch(
            "app.self_improvement.self_improvement_orchestrator.EvolutionPolicy.get_active_policy",
            new_callable=AsyncMock,
            return_value={"dryRun": True},
        ), patch(
            "app.database.database.get_db",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ), patch.object(ctx, "persist", new_callable=AsyncMock):
            result = asyncio.run(
                SelfImprovementOrchestrator.run_post_execution_cycle(
                    execution_id=eid,
                    objective="Design a test system",
                    status="completed",
                    ctx=ctx,
                    emit_fn=emit,
                )
            )

        assert result.get("sessionId")
        assert result.get("dryRun") is True
        types = [c[0][1] for c in emit.call_args_list]
        assert "improvement_cycle_started" in types
        assert "improvement_cycle_completed" in types

    def test_preflight_rejects_unsafe(self):
        with patch(
            "app.self_improvement.improvement_safety.ImprovementMemoryStore.count_today",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.self_improvement.improvement_safety.EvolutionPolicy.get_active_policy",
            new_callable=AsyncMock,
            return_value={"dryRun": True},
        ):
            pre = asyncio.run(
                ImprovementSafety.preflight(
                    {"global_retry_count": 0},
                    [{"type": "source_rewrite", "confidence": 0.9, "modifiesSourceCode": True}],
                )
            )
        assert len(pre["rejectedActions"]) >= 1

    def test_explain_cycle(self):
        reasoning = SelfImprovementOrchestrator._explain_cycle(
            {"heuristic": {}}, dry_run=True
        )
        assert reasoning["modifiesSourceCode"] is False
        assert reasoning["replayable"] is True
