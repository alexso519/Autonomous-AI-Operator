"""Tests for knowledge graph reasoning."""

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
from app.intelligence.entity_memory import EntityMemory
from app.intelligence.graph_reasoner import GraphReasoner
from app.intelligence.knowledge_graph import KnowledgeGraph
from app.intelligence.multi_hop_reasoner import MultiHopReasoner
from app.intelligence.relationship_inference import RelationshipInference


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
        await KnowledgeGraph.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestGraphReasoning:

    def test_graph_traversal_and_paths(self, temp_db):
        async def run():
            e1 = await EntityMemory.upsert_entity("AlphaCorp")
            e2 = await EntityMemory.upsert_entity("BetaInc")
            await KnowledgeGraph.upsert_edge(e1, e2, "partners_with", weight=0.8, evidence_weight=0.75)
            await KnowledgeGraph.sync_from_entity_graph()
            result = await GraphReasoner.traverse_from_entities([e1], query="AlphaCorp partners")
            return e1, e2, result

        e1, e2, result = asyncio.run(run())
        assert e1 and e2
        assert "paths" in result

    def test_relationship_inference_pattern(self, temp_db):
        async def run():
            text = "High latency causes slower inference in local LLM stacks."
            inferred = await RelationshipInference.infer_from_text(text)
            return inferred

        inferred = asyncio.run(run())
        assert isinstance(inferred, list)

    def test_multi_hop_objective_reasoning(self, temp_db):
        async def run():
            return await MultiHopReasoner.reason_for_objective(
                "OpenAI and Microsoft Azure partnerships",
            )

        result = asyncio.run(run())
        assert result.get("enabled") is True
