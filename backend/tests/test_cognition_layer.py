"""Tests for cognitive architecture layer."""

import asyncio
import os
import sys
import tempfile
import uuid
from types import ModuleType

import pytest

# Stub crewai before app.execution package loads engine
crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.cognition.benchmark_analyzer import BenchmarkAnalyzer
from app.cognition.collaboration_protocol import CollaborationProtocol
from app.cognition.consensus_manager import ConsensusManager
from app.cognition.debate_engine import DebateEngine
from app.cognition.deliberation_engine import DeliberationEngine, MAX_TOT_DEPTH
from app.cognition.hypothesis_manager import HypothesisManager
from app.cognition.self_verification import SelfVerification
from app.cognition.uncertainty_engine import UncertaintyEngine
from app.execution.context_manager import ContextManager


CYBER_OBJECTIVE = (
    "Design a scalable AI cybersecurity monitoring platform"
)


class TestHypothesisManager:

    def test_generates_architecture_hypotheses(self):
        hyps = HypothesisManager.generate(CYBER_OBJECTIVE)
        assert len(hyps) >= 3
        assert any("security" in " ".join(h.tags).lower() or "security" in h.statement.lower() for h in hyps)


class TestUncertaintyEngine:

    def test_high_stakes_raises_uncertainty(self):
        assessment = UncertaintyEngine.assess(CYBER_OBJECTIVE, [])
        assert assessment.overall >= 0.1
        assert "decision_stakes" in assessment.dimensions


class TestDebateEngine:

    def test_architecture_debate_produces_synthesis(self):
        hyps = [h.to_dict() for h in HypothesisManager.generate(CYBER_OBJECTIVE)]
        debate = DebateEngine.run_architecture_debate(CYBER_OBJECTIVE, hyps)
        assert len(debate.rounds) >= 3
        assert debate.synthesis
        assert debate.winner


class TestSelfVerification:

    def test_verifies_rich_consensus(self):
        text = (
            "Design a scalable AI cybersecurity monitoring platform using "
            "event-driven architecture with explicit cost, performance, and "
            "security tradeoffs. Phase rollout reduces risk."
        )
        result = SelfVerification.verify_consensus(text, CYBER_OBJECTIVE)
        assert result.passed

    def test_fails_short_consensus(self):
        result = SelfVerification.verify_consensus("ok", CYBER_OBJECTIVE)
        assert not result.passed


class TestDeliberationEngine:

    def test_deliberate_cybersecurity_scenario(self):
        ctx = ContextManager(execution_id=f"test-{uuid.uuid4().hex[:8]}")
        events: list[tuple[str, str]] = []

        async def emit(execution_id, event_type, agent, message, **data):
            events.append((event_type, message))

        result = asyncio.run(
            DeliberationEngine.deliberate(
                execution_id="exec-test",
                objective=CYBER_OBJECTIVE,
                ctx=ctx,
                emit_fn=emit,
            )
        )

        assert len(result.hypotheses) >= 3
        assert len(result.branches) >= 3
        assert result.best_branch is not None
        assert result.debate is not None
        assert result.consensus is not None
        assert result.uncertainty.get("overall") is not None
        assert result.reasoning_graph.get("maxDepth") == MAX_TOT_DEPTH

        event_types = {e[0] for e in events}
        assert "hypothesis_generated" in event_types
        assert "reasoning_branch_created" in event_types
        assert "debate_started" in event_types
        assert "consensus_reached" in event_types


class TestCollaboration:

    def test_voting_and_arbitration(self):
        proposals = [
            {"id": "a", "agent": "path-a", "weight": 0.8},
            {"id": "b", "agent": "path-b", "weight": 0.5},
        ]
        vote = CollaborationProtocol.vote(proposals)
        assert vote["winner"] == "a"
        arb = CollaborationProtocol.arbitrate(vote, uncertainty=0.3)
        assert arb["decision"] == "accept_winner"


class TestConsensus:

    def test_build_from_debate(self):
        hyps = [h.to_dict() for h in HypothesisManager.generate(CYBER_OBJECTIVE)]
        debate = DebateEngine.run_architecture_debate(CYBER_OBJECTIVE, hyps)
        consensus = ConsensusManager.build_from_debate(
            debate.to_dict(),
            {"label": "Security-first", "score": 0.78},
            uncertainty=0.4,
        )
        assert consensus.statement
        assert consensus.confidence > 0.5


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("app.config.settings.settings.database_path", path)
    monkeypatch.setattr("app.database.database._db", None)

    async def setup():
        from app.database.database import get_db
        from app.cognition.cognitive_memory_store import CognitiveMemoryStore
        await get_db()
        await CognitiveMemoryStore.ensure_tables()

    asyncio.run(setup())
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class TestCognitiveMemoryStore:

    def test_store_and_retrieve(self, temp_db):
        from app.cognition.cognitive_memory_store import CognitiveMemoryStore

        async def run():
            await CognitiveMemoryStore.store_workflow_pattern(
                "security-first",
                {"approach": "zero-trust"},
                execution_id="exec-1",
            )
            return await CognitiveMemoryStore.retrieve(
                "scalable cybersecurity monitoring platform",
                memory_types=["workflow"],
                limit=3,
            )

        results = asyncio.run(run())
        assert isinstance(results, list)
