"""Tests for retrieval-augmented cognition."""

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
from app.intelligence.cognitive_retriever import CognitiveRetriever
from app.intelligence.knowledge_planner import KnowledgePlanner
from app.intelligence.knowledge_reasoning_coordinator import KnowledgeReasoningCoordinator
from app.intelligence.reasoning_context_builder import ReasoningContextBuilder
from app.intelligence.world_state_predictor import WorldStatePredictor


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
        await KnowledgeReasoningCoordinator.ensure_all_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestCognitiveRetrieval:

    def test_cognitive_retriever_depth(self, temp_db):
        async def run():
            high_unc = await CognitiveRetriever.retrieve_for_cognition(
                "SQLite vs PostgreSQL for AI apps",
                uncertainty=0.7,
            )
            low_unc = await CognitiveRetriever.retrieve_for_cognition(
                "SQLite vs PostgreSQL for AI apps",
                confidence=0.9,
                uncertainty=0.1,
            )
            return high_unc, low_unc

        high, low = asyncio.run(run())
        assert high.get("enabled") is True
        assert high.get("retrievalDepth", 0) >= low.get("retrievalDepth", 0)

    def test_knowledge_planner(self, temp_db):
        async def run():
            return await KnowledgePlanner.plan_with_knowledge(
                "Research autonomous AI operator deployment strategies",
            )

        plan = asyncio.run(run())
        assert plan.get("enabled") is True
        assert "recommendedStructure" in plan

    def test_reasoning_context_builder(self, temp_db):
        async def run():
            return await ReasoningContextBuilder.build_context_block(
                "Plan a local AI deployment",
                confidence=0.6,
                uncertainty=0.4,
            )

        block = asyncio.run(run())
        assert isinstance(block, str)

    def test_world_state_predictor(self, temp_db):
        async def run():
            from app.intelligence.entity_memory import EntityMemory

            await EntityMemory.upsert_entity("Ollama")
            await EntityMemory.upsert_entity("Docker")
            return await WorldStatePredictor.predict_from_objective(
                "Ollama Docker deployment",
            )

        state = asyncio.run(run())
        assert state.get("enabled") is True

    def test_coordinator_pre_post(self, temp_db):
        async def run():
            pre = await KnowledgeReasoningCoordinator.run_pre_execution(
                "exec-1", "Analyze knowledge graph reasoning capabilities",
            )
            post = await KnowledgeReasoningCoordinator.run_post_execution(
                "exec-1",
                "Analyze knowledge graph reasoning capabilities",
                "Graph reasoning enables multi-hop entity traversal.",
                status="completed",
            )
            return pre, post

        pre, post = asyncio.run(run())
        assert pre.get("enabled") is True
        assert post.get("enabled") is True
