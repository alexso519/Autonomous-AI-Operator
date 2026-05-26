"""Tests for adaptive planning, graph, confidence, and strategy selection."""

import sys
from types import ModuleType

import pytest

# Stub crewai before app.execution package loads engine
crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.execution.adaptive_graph import AdaptiveExecutionGraph
from app.execution.confidence_engine import ConfidenceEngine, ConfidenceAction
from app.execution.context_manager import ContextManager
from app.execution.dynamic_planner import DynamicPlanner
from app.execution.hierarchical_memory import HierarchicalMemory
from app.execution.strategy_selector import StrategySelector
from app.execution.final_synthesizer import FinalResponseSynthesizer, _detect_conflicts


def _sample_nodes():
    return [
        {
            "id": "agent-0",
            "type": "agent",
            "data": {
                "label": "Researcher",
                "goal": "Research topic A",
                "dependencies": [],
            },
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "agent-1",
            "type": "agent",
            "data": {
                "label": "Analyst",
                "goal": "Analyze findings",
                "dependencies": ["agent-0"],
            },
            "position": {"x": 0, "y": 200},
        },
    ]


def test_adaptive_graph_ready_nodes_respects_dependencies():
    edges = [
        {"id": "e0", "source": "agent-0", "target": "agent-1"},
    ]
    graph = AdaptiveExecutionGraph(_sample_nodes(), edges)
    ready = graph.get_ready_nodes()
    assert len(ready) == 1
    assert ready[0]["id"] == "agent-0"

    graph.mark_completed("agent-0")
    ready = graph.get_ready_nodes()
    assert len(ready) == 1
    assert ready[0]["id"] == "agent-1"


def test_adaptive_graph_parallel_layers():
    nodes = [
        {
            "id": "a",
            "type": "agent",
            "data": {"label": "A", "dependencies": []},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "b",
            "type": "agent",
            "data": {"label": "B", "dependencies": []},
            "position": {"x": 100, "y": 0},
        },
        {
            "id": "c",
            "type": "agent",
            "data": {"dependencies": ["a", "b"]},
            "position": {"x": 50, "y": 100},
        },
    ]
    graph = AdaptiveExecutionGraph(nodes, [])
    layers = graph.get_execution_layers()
    assert len(layers[0]) == 2
    assert graph.can_parallelize(["a", "b"])


def test_graph_spawn_child_agent():
    graph = AdaptiveExecutionGraph(_sample_nodes(), [])
    parent = _sample_nodes()[0]
    child = graph.spawn_child_agent(parent, "Verify sources for topic A")
    assert child["id"] in graph.nodes
    assert child["data"]["executionMetadata"]["subgoal"] is True


def test_strategy_selector_picks_research_strategy():
    objective = "Research NVIDIA Blackwell and generate investment strategy"
    candidates = StrategySelector.generate_candidates(objective, _sample_nodes(), "complex")
    best = StrategySelector.select_best(candidates)
    assert best.composite_score > 0
    assert len(candidates) >= 2


def test_confidence_low_triggers_grounding():
    ctx = ContextManager(execution_id="test-conf")
    node = _sample_nodes()[0]
    ctx.add_output(
        node["id"],
        "Researcher",
        "I think maybe the market could possibly grow but I'm not sure about facts.",
    )
    assessment = ConfidenceEngine.assess(node, ctx)
    assert 0 <= assessment.score <= 1
    if assessment.score < ConfidenceEngine.LOW_THRESHOLD:
        assert assessment.recommended_action in (
            ConfidenceAction.TOOL_GROUNDING,
            ConfidenceAction.CLARIFICATION_RETRY,
            ConfidenceAction.ALTERNATE_REASONING,
            ConfidenceAction.SYNTHESIS_FALLBACK,
        )


def test_hierarchical_memory_tiers():
    ctx = ContextManager(execution_id="test-mem")
    mem = HierarchicalMemory(ctx)
    mem.set_working("focus", "NVIDIA Blackwell")
    mem.record_execution("agent-0", "Researcher", "Output text here.")
    mem.summarize_to_long_term("Researcher", "Summary of findings.")
    block = mem.get_context_block()
    assert "NVIDIA" in block or "Summary" in block


def test_dynamic_planner_extracts_subgoals():
    objective = (
        "Research NVIDIA Blackwell and generate investment strategy "
        "and analyze competitive landscape"
    )
    subgoals = DynamicPlanner._extract_subgoals(objective)
    assert isinstance(subgoals, list)


def test_synthesizer_detects_conflicts():
    conflicts = _detect_conflicts([
        ("Bull Agent", "Strong growth and bullish outlook for the sector."),
        ("Bear Agent", "Decline and bearish underperform risk ahead."),
    ])
    assert len(conflicts) >= 1


def test_final_synthesizer_includes_provenance():
    ctx = ContextManager(execution_id="test-syn")
    ctx.set_workflow_memory(
        "selected_strategy",
        {"label": "Parallel Research", "compositeScore": 0.82},
    )
    ctx.set_workflow_memory(
        "confidence_history",
        [{"nodeId": "agent-0", "score": 0.75}],
    )
    steps = [
        {
            "nodeId": "agent-0",
            "agentName": "Researcher",
            "status": "completed",
            "output": "NVIDIA Blackwell offers significant AI compute improvements. " * 5,
        },
    ]
    result = FinalResponseSynthesizer.synthesize(
        workflow_name="Test",
        objective="Research NVIDIA",
        steps=steps,
        ctx=ctx,
    )
    assert "Executive Summary" in result.markdown
    assert result.provenance is not None
