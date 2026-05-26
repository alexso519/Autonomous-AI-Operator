"""Tests for autonomous knowledge synthesis."""

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
from app.intelligence.concept_mapper import ConceptMapper
from app.intelligence.domain_abstraction import DomainAbstraction
from app.intelligence.knowledge_synthesizer import KnowledgeSynthesizer
from app.intelligence.research_synthesizer import ResearchSynthesizer


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
        await KnowledgeSynthesizer.ensure_tables()
        await DomainAbstraction.ensure_tables()
        await ConceptMapper.ensure_tables()

    asyncio.run(setup())
    yield
    db_mod._db = None
    try:
        os.unlink(path)
    except OSError:
        pass


class TestKnowledgeSynthesis:

    def test_synthesize_from_sources(self, temp_db):
        async def run():
            return await KnowledgeSynthesizer.synthesize_from_sources(
                "LLM inference",
                [
                    "Local LLMs run on Ollama with Docker.",
                    "Ollama supports multiple model formats.",
                ],
            )

        result = asyncio.run(run())
        assert result is not None
        assert result.get("summary")

    def test_domain_pattern_recording(self, temp_db):
        async def run():
            return await DomainAbstraction.record_pattern(
                "Research the latest AI safety guidelines",
                success=True,
            )

        pattern = asyncio.run(run())
        assert pattern is not None
        assert pattern.get("domain") == "research"

    def test_concept_discovery(self, temp_db):
        async def run():
            return await ConceptMapper.discover_concepts(
                "OpenAI released GPT-4 with Microsoft partnership.",
            )

        concepts = asyncio.run(run())
        assert isinstance(concepts, list)

    def test_research_consolidation(self, temp_db):
        async def run():
            findings = [{"text": "Source A says growth.", "confidence": 0.7}]
            return await ResearchSynthesizer.consolidate_research(
                "market outlook", findings,
            )

        result = asyncio.run(run())
        assert result.get("enabled") is True
