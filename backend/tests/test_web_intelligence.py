"""Tests for web intelligence, tool orchestration, and evidence graph."""

import sys
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.execution.evidence_graph import EvidenceGraph
from app.execution.context_manager import ContextManager
from app.tools.source_ranker import SourceRanker
from app.tools.citation_manager import CitationManager
from app.tools.tool_orchestrator import ToolOrchestrator, _compare_viewpoints
from app.execution.research_agents import is_research_objective, apply_research_profiles


def test_source_ranker_dedupes_and_scores():
    results = [
        {"title": "NVIDIA Blackwell", "url": "https://nvidia.com/a", "snippet": "Blackwell GPU architecture"},
        {"title": "NVIDIA Blackwell", "url": "https://nvidia.com/a", "snippet": "Blackwell GPU architecture"},
        {"title": "Market outlook", "url": "https://example.com/b", "snippet": "Bearish risk decline"},
    ]
    ranked = SourceRanker.rank_search_results("NVIDIA Blackwell investment", results)
    assert len(ranked) >= 2
    primaries = SourceRanker.collapse_duplicates(ranked)
    assert any(p.duplicate_of for p in ranked) or len(primaries) < len(ranked)


def test_citation_manager_bibliography():
    mgr = CitationManager()
    c = mgr.add_from_source("s1", "NVIDIA Blog", "https://nvidia.com", "Blackwell improves AI compute")
    mgr.link_claim("Blackwell improves AI compute", [c.id], confidence=0.8)
    bib = mgr.format_bibliography()
    assert "[1]" in bib
    assert "NVIDIA" in bib


def test_evidence_graph_conflict_detection():
    graph = EvidenceGraph()
    graph.add_claim("Strong growth and bullish outlook for NVIDIA", [])
    graph.add_claim("Decline and bearish underperform risk ahead", [])
    conflicts = graph.detect_conflicts()
    assert len(conflicts) >= 1


def test_compare_viewpoints():
    from app.tools.source_ranker import RankedSource

    sources = [
        RankedSource("1", "A", "", "bullish growth strong", "a.com", 0.5, 0.5, 0.5, fetch_text="bullish growth"),
        RankedSource("2", "B", "", "bearish decline risk", "b.com", 0.5, 0.5, 0.5, fetch_text="bearish decline"),
    ]
    cmp = _compare_viewpoints(sources)
    assert cmp["conflictLikely"] is True


def test_tool_orchestrator_plan_research():
    plan = ToolOrchestrator.plan_research_chain("NVIDIA Blackwell investment outlook")
    assert plan.name == "web_research_pipeline"
    assert plan.steps[0].tool_name == "web_search"
    assert plan.estimated_usefulness > 0.6


def test_research_objective_detection():
    assert is_research_objective("Research NVIDIA Blackwell investment outlook")
    assert not is_research_objective("Say hello")


def test_apply_research_profiles():
    nodes = [
        {
            "id": "agent-0",
            "type": "agent",
            "data": {"label": "Agent", "goal": "do work"},
            "position": {"x": 0, "y": 0},
        },
    ]
    updated = apply_research_profiles(nodes, "Research NVIDIA Blackwell")
    assert updated[0]["data"]["label"] == "ResearchScout"
