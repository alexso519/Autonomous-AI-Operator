"""
Multi-agent collaboration: voting, arbitration, role negotiation, blackboard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class BlackboardEntry:
    key: str
    value: Any
    author: str
    priority: float = 0.5
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "author": self.author,
            "priority": round(self.priority, 3),
            "timestamp": self.timestamp,
        }


class CollaborationProtocol:
    """Blackboard coordination and delegated reasoning roles."""

    ROLES = ("planner", "researcher", "critic", "synthesizer", "verifier")

    @classmethod
    def negotiate_roles(
        cls,
        objective: str,
        agent_count: int,
    ) -> dict[str, str]:
        objective_lower = objective.lower()
        roles: dict[str, str] = {}

        if agent_count >= 1:
            roles["agent-0"] = "planner"
        if agent_count >= 2:
            roles["agent-1"] = (
                "researcher"
                if any(k in objective_lower for k in ("research", "analyze", "monitor"))
                else "critic"
            )
        if agent_count >= 3:
            roles["agent-2"] = "synthesizer"
        if agent_count >= 4:
            roles["agent-3"] = "verifier"

        return roles

    @classmethod
    def create_blackboard(cls) -> dict[str, Any]:
        return {
            "id": f"bb-{uuid.uuid4().hex[:8]}",
            "entries": [],
            "delegations": [],
        }

    @classmethod
    def post(
        cls,
        blackboard: dict[str, Any],
        entry: BlackboardEntry,
    ) -> dict[str, Any]:
        entries = list(blackboard.get("entries") or [])
        entries.append(entry.to_dict())
        blackboard["entries"] = entries[-50:]
        return blackboard

    @classmethod
    def vote(
        cls,
        proposals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not proposals:
            return {"winner": None, "tally": {}}

        tally: dict[str, float] = {}
        for p in proposals:
            pid = p.get("id", p.get("agent", "unknown"))
            tally[pid] = tally.get(pid, 0) + float(p.get("weight", 1.0))

        winner = max(tally, key=tally.get)
        return {"winner": winner, "tally": tally, "proposalCount": len(proposals)}

    @classmethod
    def arbitrate(
        cls,
        vote_result: dict[str, Any],
        uncertainty: float,
    ) -> dict[str, Any]:
        winner = vote_result.get("winner")
        if uncertainty > 0.6 and winner:
            return {
                "decision": "defer_to_debate",
                "winner": winner,
                "reason": "High uncertainty — escalate to structured debate",
            }
        return {
            "decision": "accept_winner" if winner else "no_consensus",
            "winner": winner,
            "reason": "Vote threshold met" if winner else "Insufficient proposals",
        }

    @classmethod
    def delegate_reasoning(
        cls,
        blackboard: dict[str, Any],
        from_agent: str,
        to_agent: str,
        subtask: str,
    ) -> dict[str, Any]:
        delegations = list(blackboard.get("delegations") or [])
        delegations.append(
            {
                "id": f"del-{uuid.uuid4().hex[:8]}",
                "from": from_agent,
                "to": to_agent,
                "subtask": subtask[:300],
                "status": "pending",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        blackboard["delegations"] = delegations[-20:]
        return blackboard
