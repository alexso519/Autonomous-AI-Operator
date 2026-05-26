"""Tests for persistent world model."""

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
from app.intelligence.fact_evolution import FactEvolution
from app.intelligence.world_model import WorldModel


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
        await WorldModel.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestEntityMemory:

    def test_extract_entities(self):
        text = "OpenAI released GPT-4 and Microsoft integrated Azure OpenAI."
        entities = EntityMemory.extract_entity_candidates(text)
        assert len(entities) >= 1

    def test_upsert_and_graph(self, temp_db):
        async def run():
            e1 = await EntityMemory.upsert_entity("OpenAI", entity_type="org")
            e2 = await EntityMemory.upsert_entity("Microsoft", entity_type="org")
            await EntityMemory.link_entities(e1, e2, "partners_with")
            return e1, e2, await EntityMemory.get_graph()

        e1, e2, graph = asyncio.run(run())
        assert e1 and e2
        assert len(graph["entities"]) >= 2
        assert len(graph["relationships"]) >= 1


class TestFactEvolution:

    def test_fact_creation_and_evolution(self, temp_db):
        async def run():
            r1 = await FactEvolution.upsert_fact("Ollama", "runs locally on Docker")
            r2 = await FactEvolution.upsert_fact("Ollama", "runs locally on Docker")
            r3 = await FactEvolution.upsert_fact("Ollama", "does not run locally on Docker")
            timeline = await FactEvolution.get_timeline(r1["factId"])
            return r1, r2, r3, timeline

        r1, r2, r3, timeline = asyncio.run(run())
        assert r1["status"] == "created"
        assert r2["status"] == "reinforced"
        assert r3["status"] in ("evolved", "contradicted")
        assert len(timeline) >= 2


class TestWorldModel:

    def test_ingest_execution_text(self, temp_db):
        async def run():
            result = await WorldModel.ingest_execution_text(
                "PostgreSQL is a relational database. SQLite is embedded.",
                execution_id="wm-test",
            )
            snapshot = await WorldModel.get_snapshot()
            return result, snapshot

        result, snapshot = asyncio.run(run())
        assert len(result["entities"]) >= 1
        assert snapshot["enabled"] is True
