"""
Internal debate between reasoning perspectives (cost / performance / security).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class DebateRound:
    perspective: str
    argument: str
    stance: str  # support | oppose | neutral
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "perspective": self.perspective,
            "argument": self.argument,
            "stance": self.stance,
            "weight": round(self.weight, 3),
        }


@dataclass
class DebateSession:
    id: str
    topic: str
    rounds: list[DebateRound] = field(default_factory=list)
    winner: str | None = None
    synthesis: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "rounds": [r.to_dict() for r in self.rounds],
            "winner": self.winner,
            "synthesis": self.synthesis,
            "createdAt": self.created_at,
        }


class DebateEngine:
    """Structured multi-perspective debate — bounded rounds."""

    MAX_ROUNDS = 6
    PERSPECTIVES = (
        ("cost_optimizer", "Minimize infrastructure and operational spend", "oppose"),
        ("performance_engineer", "Maximize throughput and low-latency detection", "support"),
        ("security_architect", "Prioritize zero-trust, encryption, and auditability", "support"),
    )

    @classmethod
    def run_architecture_debate(
        cls,
        objective: str,
        hypotheses: list[dict[str, Any]],
    ) -> DebateSession:
        session_id = f"debate-{uuid.uuid4().hex[:8]}"
        topic = objective[:200]
        rounds: list[DebateRound] = []

        top_hyp = max(
            hypotheses,
            key=lambda h: float(h.get("confidence", 0)),
            default={"statement": "baseline approach"},
        )
        focal = str(top_hyp.get("statement", ""))[:160]

        for perspective, base_arg, default_stance in cls.PERSPECTIVES:
            arg = cls._craft_argument(perspective, focal, base_arg)
            weight = cls._perspective_weight(perspective, hypotheses)
            rounds.append(
                DebateRound(
                    perspective=perspective,
                    argument=arg,
                    stance=default_stance,
                    weight=weight,
                )
            )

        winner = max(rounds, key=lambda r: r.weight).perspective
        synthesis = cls._synthesize_debate(focal, rounds, winner)

        return DebateSession(
            id=session_id,
            topic=topic,
            rounds=rounds[: cls.MAX_ROUNDS],
            winner=winner,
            synthesis=synthesis,
        )

    @classmethod
    def run_generic_debate(cls, objective: str) -> DebateSession:
        session_id = f"debate-{uuid.uuid4().hex[:8]}"
        rounds = [
            DebateRound(
                perspective="analyst",
                argument=f"Evidence-first approach for: {objective[:100]}",
                stance="support",
                weight=0.7,
            ),
            DebateRound(
                perspective="skeptic",
                argument="Verify claims with tools before committing to conclusions",
                stance="oppose",
                weight=0.65,
            ),
        ]
        return DebateSession(
            id=session_id,
            topic=objective[:200],
            rounds=rounds,
            winner="analyst",
            synthesis=f"Balanced plan: tool-grounded analysis of {objective[:80]}",
        )

    @classmethod
    def _craft_argument(cls, perspective: str, focal: str, base: str) -> str:
        if perspective == "cost_optimizer":
            return (
                f"{base}. For '{focal[:60]}', favor managed services and "
                "tiered storage to control spend."
            )
        if perspective == "performance_engineer":
            return (
                f"{base}. '{focal[:60]}' benefits from streaming pipelines "
                "and horizontal worker pools."
            )
        return (
            f"{base}. '{focal[:60]}' requires encryption in transit/at rest, "
            "RBAC, and immutable audit logs."
        )

    @classmethod
    def _perspective_weight(
        cls, perspective: str, hypotheses: list[dict[str, Any]]
    ) -> float:
        tags = []
        for h in hypotheses:
            tags.extend(h.get("tags") or [])
        tag_str = " ".join(tags).lower()
        if perspective == "security_architect" and "security" in tag_str:
            return 0.82
        if perspective == "performance_engineer" and "scalability" in tag_str:
            return 0.78
        if perspective == "cost_optimizer" and "cost" in tag_str:
            return 0.75
        return 0.68

    @classmethod
    def _synthesize_debate(
        cls, focal: str, rounds: list[DebateRound], winner: str
    ) -> str:
        weights = {r.perspective: r.weight for r in rounds}
        return (
            f"Debate synthesis (leading perspective: {winner}): "
            f"Adopt '{focal[:80]}' with explicit tradeoffs — "
            f"cost weight {weights.get('cost_optimizer', 0):.0%}, "
            f"performance {weights.get('performance_engineer', 0):.0%}, "
            f"security {weights.get('security_architect', 0):.0%}. "
            "Recommend phased rollout: MVP monolith or modular core, "
            "then scale ingestion and ML correlation as volume grows."
        )
