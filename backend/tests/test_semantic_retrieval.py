"""Tests for semantic retrieval pipeline."""

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
from app.intelligence.knowledge_indexer import KnowledgeIndexer
from app.intelligence.semantic_retriever import SemanticRetriever
from app.intelligence.vector_memory import VectorMemory


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
        await VectorMemory.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestSemanticRetriever:

    def test_retrieve_for_objective(self, temp_db):
        async def run():
            await VectorMemory.store(
                "Key finding: use WAL mode for SQLite concurrent reads",
                "research",
                metadata={"tags": ["sqlite", "performance"]},
            )
            await VectorMemory.store(
                "Docker Compose simplifies Ollama deployment",
                "research",
                metadata={"tags": ["ollama", "docker"]},
            )
            return await SemanticRetriever.retrieve_for_objective(
                "SQLite performance tuning",
                limit=3,
            )

        results = asyncio.run(run())
        assert len(results) >= 1
        assert "sqlite" in results[0]["content"].lower() or "WAL" in results[0]["content"]

    def test_format_context_block(self, temp_db):
        memories = [{"memoryType": "research", "content": "test", "combinedScore": 0.8}]
        block = SemanticRetriever.format_context_block(memories)
        assert "[Long-term semantic memory]" in block


class TestKnowledgeIndexer:

    def test_index_tool_result(self, temp_db):
        async def run():
            mid = await KnowledgeIndexer.index_tool_result(
                "exec-idx",
                "calculator",
                {"result": 42},
            )
            results = await VectorMemory.search("calculator result")
            return mid, results

        mid, results = asyncio.run(run())
        assert mid is not None
        assert len(results) >= 1
