"""Tests for vector memory and embedding infrastructure."""

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
from app.intelligence.embedding_service import EmbeddingService, cosine_similarity, _hash_embedding
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
    yield path
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestEmbeddingService:

    def test_hash_embedding_deterministic(self):
        a = _hash_embedding("hello world")
        b = _hash_embedding("hello world")
        assert a == b
        assert len(a) == 384

    def test_cosine_similarity_identical(self):
        v = _hash_embedding("test")
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=0.01)

    def test_embed_uses_cache(self, temp_db):
        async def run():
            e1 = await EmbeddingService.embed("cache test content")
            e2 = await EmbeddingService.embed("cache test content")
            return e1, e2

        e1, e2 = asyncio.run(run())
        assert e1 == e2


class TestVectorMemory:

    def test_store_and_search(self, temp_db):
        async def run():
            mem_id = await VectorMemory.store(
                "SQLite is great for single-user AI apps",
                "research",
                execution_id="exec-test",
            )
            results = await VectorMemory.search("SQLite single user database")
            return mem_id, results

        mem_id, results = asyncio.run(run())
        assert mem_id is not None
        assert len(results) >= 1
        assert results[0]["id"] == mem_id

    def test_retention_limit(self, temp_db, monkeypatch):
        monkeypatch.setattr(settings, "max_long_term_memories", 3)

        async def run():
            for i in range(5):
                await VectorMemory.store(f"memory content number {i}", "execution")
            import app.database.database as db_mod

            db = await db_mod.get_db()
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM vector_memories")
            return (await cursor.fetchone())["cnt"]

        count = asyncio.run(run())
        assert count <= 3
