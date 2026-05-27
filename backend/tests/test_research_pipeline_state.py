"""Tests for research pipeline readiness helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.execution.context_manager import ContextManager
from app.execution.research_memory import ResearchMemory
from app.execution.research_pipeline_state import (
    is_research_pipeline_ready,
    research_pipeline_context_block,
)


def test_pipeline_ready_from_research_memory_sources():
    ctx = ContextManager(execution_id="test-pipeline-ready")
    ResearchMemory(ctx).store_sources([
        {"title": "NVIDIA Blackwell", "url": "https://nvidia.com", "snippet": "AI factory"},
    ])

    assert is_research_pipeline_ready(ctx)
    block = research_pipeline_context_block(ctx)
    assert "Pre-collected Research Evidence" in block
    assert "NVIDIA Blackwell" in block
