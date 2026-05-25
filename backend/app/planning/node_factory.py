from typing import Any

from app.services.task_analyzer import RequiredAgent, RiskLevel


def create_agent_node(
    agent: RequiredAgent,
    sequence_order: int,
    dependencies: list[str],
    position: dict[str, int],
    risk_level: RiskLevel,
) -> dict[str, Any]:
    """Build a deterministic agent node for the workflow graph."""
    return {
        "id": f"agent-{sequence_order}",
        "type": "agent",
        "data": {
            "label": agent.role,
            "role": agent.role,
            "goal": agent.goal,
            "taskDescription": agent.goal,
            "backstory": agent.backstory,
            "dependencies": dependencies,
            "requiresApproval": False,
            "nodeType": "agent",
            "executionMetadata": {
                "sequenceOrder": sequence_order,
                "requiresApproval": False,
                "riskLevel": risk_level.value,
                "createdBy": "WorkflowPlanner",
            },
            "temperature": 0.7,
        },
        "position": position,
    }


def create_approval_node(
    reason: str,
    sequence_order: int,
    position: dict[str, int],
) -> dict[str, Any]:
    """Build an approval node that can pause execution until a human approves."""
    return {
        "id": f"approval-{sequence_order}",
        "type": "approval",
        "data": {
            "label": "Human Approval Required",
            "nodeType": "approval",
            "description": reason,
            "dependencies": [],
            "requiresApproval": True,
            "executionMetadata": {
                "sequenceOrder": sequence_order,
                "requiresApproval": True,
                "createdBy": "WorkflowPlanner",
            },
        },
        "position": position,
    }


def create_edge(source: str, target: str, index: int) -> dict[str, str]:
    """Build a workflow edge linking two graph nodes."""
    return {
        "id": f"edge-{index}",
        "source": source,
        "target": target,
    }
