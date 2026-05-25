"""
Quick test of the deterministic WorkflowPlanner behavior.

Run with: python -m pytest backend/tests/test_workflow_planner.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.planning import get_planner
from app.services.task_analyzer import (
    RequiredAgent,
    TaskAnalysis,
    TaskType,
    Complexity,
    RiskLevel,
)


def test_workflow_planner_low_risk():
    planner = get_planner()
    analysis = TaskAnalysis(
        objective="Write a simple blog post about AI",
        task_type=TaskType.CONTENT_CREATION,
        complexity=Complexity.SIMPLE,
        risk_level=RiskLevel.LOW,
        required_agents=[
            RequiredAgent(role="Content Writer", goal="Draft the article", sequence_order=0),
            RequiredAgent(role="Editor", goal="Proofread and polish the article", sequence_order=1),
        ],
        keywords=["AI", "blog"],
        estimated_steps=2,
        requires_human_approval=False,
        reasoning="Simple content creation task.",
    )

    nodes, edges = planner.plan(analysis)

    assert len(nodes) == 2
    assert len(edges) == 1
    assert nodes[0]["type"] == "agent"
    assert nodes[1]["type"] == "agent"
    assert nodes[0]["data"]["dependencies"] == []
    assert nodes[1]["data"]["dependencies"] == ["agent-0"]
    assert edges[0]["source"] == "agent-0"
    assert edges[0]["target"] == "agent-1"


def test_workflow_planner_high_risk_requires_approval():
    planner = get_planner()
    analysis = TaskAnalysis(
        objective="Delete old server logs and update production configuration",
        task_type=TaskType.CODING,
        complexity=Complexity.MODERATE,
        risk_level=RiskLevel.HIGH,
        required_agents=[
            RequiredAgent(role="Software Engineer", goal="Perform the requested cleanup and update tasks", sequence_order=0),
            RequiredAgent(role="Code Reviewer", goal="Validate the changes before finalizing", sequence_order=1),
        ],
        keywords=["delete", "server", "configuration"],
        estimated_steps=3,
        requires_human_approval=True,
        reasoning="High-risk operation involving destructive changes.",
    )

    nodes, edges = planner.plan(analysis)

    assert len(nodes) == 3
    assert len(edges) == 2
    assert nodes[0]["type"] == "approval"
    assert nodes[1]["type"] == "agent"
    assert nodes[1]["data"]["dependencies"] == ["approval-0"]
    assert edges[0]["source"] == "approval-0"
    assert edges[0]["target"] == "agent-0"
    assert edges[1]["source"] == "agent-0"
    assert edges[1]["target"] == "agent-1"
