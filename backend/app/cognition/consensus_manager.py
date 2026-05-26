"""
Consensus building from debate outcomes and branch selection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ConsensusResult:
    id: str
    statement: str
    confidence: float
    contributing_agents: list[str] = field(default_factory=list)
    votes: dict[str, float] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "confidence": round(self.confidence, 3),
            "contributingAgents": self.contributing_agents,
            "votes": {k: round(v, 3) for k, v in self.votes.items()},
            "createdAt": self.created_at,
        }


class ConsensusManager:
    """Weighted voting and synthesis from collaboration artifacts."""

    @classmethod
    def build_from_debate(
        cls,
        debate: dict[str, Any],
        best_branch: dict[str, Any] | None,
        uncertainty: float,
    ) -> ConsensusResult:
        synthesis = debate.get("synthesis", "")
        branch_label = (best_branch or {}).get("label", "")
        branch_score = float((best_branch or {}).get("score", 0.6))

        if branch_label:
            statement = (
                f"{synthesis} Selected reasoning path: {branch_label} "
                f"(confidence {branch_score:.0%})."
            )
        else:
            statement = synthesis or "Proceed with tool-grounded sequential execution."

        votes = {
            r.get("perspective", "agent"): float(r.get("weight", 0.5))
            for r in debate.get("rounds", [])
        }
        avg_vote = sum(votes.values()) / max(len(votes), 1)
        confidence = min(0.92, (avg_vote * 0.5 + branch_score * 0.35 + (1 - uncertainty) * 0.15))

        return ConsensusResult(
            id=f"cons-{uuid.uuid4().hex[:8]}",
            statement=statement.strip(),
            confidence=confidence,
            contributing_agents=list(votes.keys()),
            votes=votes,
        )

    @classmethod
    def merge_votes(
        cls,
        proposals: list[dict[str, Any]],
    ) -> ConsensusResult:
        if not proposals:
            return ConsensusResult(
                id=f"cons-{uuid.uuid4().hex[:8]}",
                statement="No proposals to merge",
                confidence=0.3,
            )

        weighted: dict[str, float] = {}
        for p in proposals:
            agent = p.get("agent", "unknown")
            weighted[agent] = weighted.get(agent, 0) + float(p.get("weight", 0.5))

        winner = max(weighted, key=weighted.get)
        best = next((p for p in proposals if p.get("agent") == winner), proposals[0])
        total = sum(weighted.values())

        return ConsensusResult(
            id=f"cons-{uuid.uuid4().hex[:8]}",
            statement=str(best.get("statement", "")),
            confidence=min(0.9, total / max(len(proposals), 1)),
            contributing_agents=list(weighted.keys()),
            votes=weighted,
        )
