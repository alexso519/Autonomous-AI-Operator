"""
Dependency-aware execution graph with runtime mutation support.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.planning.node_factory import create_edge

logger = logging.getLogger(__name__)


@dataclass
class GraphMutation:
    action: str
    node_ids: list[str]
    edge_ids: list[str]
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "nodeIds": self.node_ids,
            "edgeIds": self.edge_ids,
            "reason": self.reason,
            "metadata": self.metadata,
        }


class AdaptiveExecutionGraph:
    """
    Mutable DAG over workflow nodes.

    Dependencies come from edges and node.data.dependencies.
    """

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        self.nodes: dict[str, dict[str, Any]] = {
            n["id"]: n for n in nodes if n.get("id")
        }
        self.edges: list[dict[str, Any]] = list(edges)
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.mutations: list[GraphMutation] = []
        self._rebuild_deps()

    def _rebuild_deps(self) -> None:
        self._deps: dict[str, set[str]] = {nid: set() for nid in self.nodes}
        for edge in self.edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt and tgt in self._deps:
                self._deps[tgt].add(src)
        for nid, node in self.nodes.items():
            for dep in node.get("data", {}).get("dependencies") or []:
                if dep in self.nodes:
                    self._deps[nid].add(dep)

    def ordered_nodes(self) -> list[dict[str, Any]]:
        """Stable topological-ish order (position fallback)."""
        ready_layers = self.get_execution_layers(include_completed=True)
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for layer in ready_layers:
            for nid in layer:
                if nid not in seen and nid in self.nodes:
                    seen.add(nid)
                    ordered.append(self.nodes[nid])
        for nid, node in self.nodes.items():
            if nid not in seen:
                ordered.append(node)
        return ordered

    def get_ready_nodes(self) -> list[dict[str, Any]]:
        """Nodes whose dependencies are all completed and not yet run."""
        ready: list[dict[str, Any]] = []
        for nid, node in self.nodes.items():
            if nid in self.completed or nid in self.failed:
                continue
            deps = self._deps.get(nid, set())
            if deps.issubset(self.completed):
                ready.append(node)
        return self._sort_by_position(ready)

    def get_execution_layers(self, include_completed: bool = False) -> list[list[str]]:
        """Partition nodes into parallelizable layers."""
        remaining = set(self.nodes.keys())
        if not include_completed:
            remaining -= self.completed
            remaining -= self.failed

        layers: list[list[str]] = []
        completed_sim = set(self.completed)

        while remaining:
            layer = [
                nid for nid in remaining
                if self._deps.get(nid, set()).issubset(completed_sim)
            ]
            if not layer:
                layer = [min(remaining, key=lambda n: self._position_key(self.nodes[n]))]
            layers.append(layer)
            for nid in layer:
                remaining.discard(nid)
                if include_completed or nid not in self.completed:
                    completed_sim.add(nid)
        return layers

    def mark_completed(self, node_id: str) -> None:
        self.completed.add(node_id)

    def mark_failed(self, node_id: str) -> None:
        self.failed.add(node_id)

    def insert_nodes(
        self,
        new_nodes: list[dict[str, Any]],
        after_node_id: str | None = None,
        reason: str = "replan",
    ) -> GraphMutation:
        edge_ids: list[str] = []
        node_ids: list[str] = []

        for node in new_nodes:
            nid = node["id"]
            self.nodes[nid] = node
            node_ids.append(nid)
            if after_node_id and after_node_id in self.nodes:
                edge = create_edge(after_node_id, nid, len(self.edges))
                self.edges.append(edge)
                edge_ids.append(edge["id"])
                deps = list(node.get("data", {}).get("dependencies") or [])
                if after_node_id not in deps:
                    deps.append(after_node_id)
                    node.setdefault("data", {})["dependencies"] = deps

        self._rebuild_deps()
        mutation = GraphMutation(
            action="insert",
            node_ids=node_ids,
            edge_ids=edge_ids,
            reason=reason,
            metadata={"afterNodeId": after_node_id},
        )
        self.mutations.append(mutation)
        logger.info("Graph mutated: inserted %s nodes (%s)", node_ids, reason)
        return mutation

    def spawn_child_agent(
        self,
        parent_node: dict[str, Any],
        subgoal: str,
        suffix: str | None = None,
    ) -> dict[str, Any]:
        parent_id = parent_node.get("id", "parent")
        pos = parent_node.get("position", {})
        child_id = f"{parent_id}-child-{suffix or uuid.uuid4().hex[:6]}"
        child = {
            "id": child_id,
            "type": "agent",
            "data": {
                "label": f"Subtask: {subgoal[:40]}",
                "role": "Subgoal Agent",
                "goal": subgoal,
                "taskDescription": subgoal,
                "backstory": (
                    "You are a focused sub-agent executing one subgoal of a larger task. "
                    "Use prior context and tools; be concise and evidence-based."
                ),
                "dependencies": [parent_id],
                "requiresApproval": False,
                "nodeType": "agent",
                "executionMetadata": {
                    "spawnedBy": "DynamicPlanner",
                    "parentNodeId": parent_id,
                    "subgoal": True,
                },
            },
            "position": {
                "x": pos.get("x", 200) + 180,
                "y": pos.get("y", 0) + 100,
            },
        }
        self.insert_nodes([child], after_node_id=parent_id, reason="child_agent_spawned")
        return child

    def can_parallelize(self, node_ids: list[str]) -> bool:
        """True if nodes share no dependency chain between each other in this batch."""
        if len(node_ids) <= 1:
            return False
        for i, a in enumerate(node_ids):
            for b in node_ids[i + 1:]:
                if b in self._deps.get(a, set()) or a in self._deps.get(b, set()):
                    return False
        return True

    @staticmethod
    def _position_key(node: dict[str, Any]) -> tuple[float, float]:
        pos = node.get("position", {})
        return (pos.get("y", 0), pos.get("x", 0))

    @classmethod
    def _sort_by_position(cls, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(nodes, key=cls._position_key)

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "nodeCount": len(self.nodes),
            "completed": list(self.completed),
            "failed": list(self.failed),
            "mutations": [m.to_dict() for m in self.mutations],
            "layers": self.get_execution_layers(include_completed=False),
        }
