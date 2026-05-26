"""
UI navigation graph — track paths between screen states.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NavigationNode:
    id: str
    state_hash: str
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NavigationEdge:
    from_node: str
    to_node: str
    action: str
    success: bool = True


class UINavigationGraph:
    """Graph of UI states and transitions for multi-step planning."""

    def __init__(self) -> None:
        self.nodes: dict[str, NavigationNode] = {}
        self.edges: list[NavigationEdge] = []
        self._current: str | None = None

    def add_state(self, state_hash: str, label: str = "", **metadata: Any) -> str:
        for nid, node in self.nodes.items():
            if node.state_hash == state_hash:
                if label:
                    node.label = label
                self._current = nid
                return nid
        nid = f"node-{uuid.uuid4().hex[:8]}"
        self.nodes[nid] = NavigationNode(id=nid, state_hash=state_hash, label=label, metadata=metadata)
        self._current = nid
        return nid

    def record_transition(
        self,
        from_hash: str,
        to_hash: str,
        action: str,
        *,
        success: bool = True,
    ) -> None:
        from_id = self._find_or_create(from_hash)
        to_id = self.add_state(to_hash)
        self.edges.append(NavigationEdge(from_node=from_id, to_node=to_id, action=action, success=success))
        self._current = to_id

    def _find_or_create(self, state_hash: str) -> str:
        for nid, node in self.nodes.items():
            if node.state_hash == state_hash:
                return nid
        return self.add_state(state_hash)

    def plan_path(self, target_label: str) -> list[str]:
        """Simple BFS path through known states matching target label."""
        target_nodes = [
            nid for nid, n in self.nodes.items()
            if target_label.lower() in n.label.lower()
        ]
        if not target_nodes or not self._current:
            return []
        target = target_nodes[0]
        if self._current == target:
            return [self._current]

        adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for edge in self.edges:
            if edge.success:
                adj[edge.from_node].append(edge.to_node)

        queue = [(self._current, [self._current])]
        visited = {self._current}
        while queue:
            node, path = queue.pop(0)
            for neighbor in adj.get(node, []):
                if neighbor == target:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeCount": len(self.nodes),
            "edgeCount": len(self.edges),
            "currentNode": self._current,
            "nodes": [
                {"id": n.id, "stateHash": n.state_hash, "label": n.label}
                for n in self.nodes.values()
            ],
            "edges": [
                {"from": e.from_node, "to": e.to_node, "action": e.action, "success": e.success}
                for e in self.edges
            ],
        }
