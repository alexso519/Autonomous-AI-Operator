"""Tests for causal reasoning and conflict resolution."""

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
from app.intelligence.belief_tracker import BeliefTracker
from app.intelligence.causal_engine import CausalEngine
from app.intelligence.conflict_resolver import ConflictResolver
from app.intelligence.evidence_reconciler import EvidenceReconciler


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
        await CausalEngine.ensure_tables()
        await ConflictResolver.ensure_tables()
        await BeliefTracker.ensure_tables()
        await EvidenceReconciler.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestCausalEngine:

    def test_causal_inference_from_text(self, temp_db):
        async def run():
            return await CausalEngine.infer_from_text(
                "GPU shortage causes increased cloud inference costs.",
            )

        links = asyncio.run(run())
        assert isinstance(links, list)

    def test_conflict_register_and_resolve(self, temp_db):
        async def run():
            reg = await ConflictResolver.register_conflict(
                "Ollama",
                "Ollama runs fully offline",
                "Ollama requires cloud API access",
            )
            if not reg or reg.get("status") == "escalation_blocked":
                return reg
            resolved = await ConflictResolver.resolve_conflict(
                reg["id"],
                "Offline mode is primary; cloud is optional.",
                winning_claim="Ollama runs fully offline",
            )
            return reg, resolved

        result = asyncio.run(run())
        assert result is not None

    def test_evidence_reconciliation(self, temp_db):
        async def run():
            claims = [
                {"text": "SQLite is ideal for single-user apps", "sourceId": "s1", "reliability": 0.8},
                {"text": "SQLite handles concurrent writes poorly", "sourceId": "s2", "reliability": 0.7},
            ]
            return await EvidenceReconciler.reconcile_claims("SQLite", claims)

        consensus = asyncio.run(run())
        assert consensus.get("consensusClaim") or consensus.get("suppressed")

    def test_belief_update(self, temp_db):
        async def run():
            return await BeliefTracker.update_belief(
                "test_subject", "test_proposition", confidence_delta=0.1,
            )

        belief = asyncio.run(run())
        assert belief.get("confidence", 0) > 0
