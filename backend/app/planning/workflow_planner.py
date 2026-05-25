from typing import Any

from app.planning.node_factory import (
    create_agent_node,
    create_approval_node,
    create_edge,
)
from app.planning.planning_rules import (
    get_approval_reason,
    requires_workflow_approval,
)
from app.planning.agent_factory import get_agent_factory
from app.services.task_analyzer import TaskAnalysis


class WorkflowPlanner:
    """Deterministic workflow planner that converts TaskAnalysis into a graph."""

    def plan(self, analysis: TaskAnalysis) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Convert analyzed task metadata into workflow nodes and edges."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        approval_required = requires_workflow_approval(analysis)
        # Generate deterministic, enriched agents from the analysis
        agent_factory = get_agent_factory()
        generated_agents = agent_factory.generate_agents(analysis)
        position_y = 100
        previous_node_id: str | None = None

        if approval_required:
            approval_node = create_approval_node(
                reason=get_approval_reason(analysis),
                sequence_order=0,
                position={"x": 200, "y": position_y},
            )
            nodes.append(approval_node)
            previous_node_id = approval_node["id"]
            position_y += 180

        for index, agent in enumerate(generated_agents):
            node_id = f"agent-{index}"
            dependencies = [previous_node_id] if previous_node_id else []
            agent_node = create_agent_node(
                agent=agent,
                sequence_order=index,
                dependencies=dependencies,
                position={"x": 200, "y": position_y},
                risk_level=analysis.risk_level,
            )
            nodes.append(agent_node)

            if previous_node_id is not None:
                edges.append(create_edge(previous_node_id, node_id, len(edges)))
            previous_node_id = node_id
            position_y += 180

        return nodes, edges


_planner_instance: WorkflowPlanner | None = None


def get_planner() -> WorkflowPlanner:
    """Return a singleton WorkflowPlanner instance."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = WorkflowPlanner()
    return _planner_instance
