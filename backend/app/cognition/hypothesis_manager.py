"""
Hypothesis generation and lifecycle for deliberative reasoning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Hypothesis:
    id: str
    statement: str
    rationale: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    status: str = "open"  # open | supported | rejected | merged
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
            "tags": self.tags,
            "status": self.status,
            "createdAt": self.created_at,
        }


class HypothesisManager:
    """Generate and track candidate hypotheses for an objective."""

    MAX_HYPOTHESES = 6

    ARCHITECTURE_KEYWORDS = (
        "architecture", "platform", "system", "design", "scalable",
        "monitoring", "security", "cybersecurity", "infrastructure",
    )
    TRADEOFF_DIMENSIONS = ("cost", "performance", "security", "reliability", "compliance")

    @classmethod
    def generate(
        cls,
        objective: str,
        context_hints: list[str] | None = None,
    ) -> list[Hypothesis]:
        objective_lower = objective.lower()
        hints = context_hints or []
        hypotheses: list[Hypothesis] = []

        is_arch = any(k in objective_lower for k in cls.ARCHITECTURE_KEYWORDS)

        if is_arch:
            hypotheses.extend(cls._architecture_hypotheses(objective))
        else:
            hypotheses.extend(cls._generic_hypotheses(objective))

        for hint in hints[:2]:
            hypotheses.append(
                Hypothesis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    statement=f"Prior pattern applies: {hint[:120]}",
                    rationale="Recovered from cognitive memory",
                    confidence=0.55,
                    tags=["memory", "pattern"],
                )
            )

        return hypotheses[: cls.MAX_HYPOTHESES]

    @classmethod
    def _architecture_hypotheses(cls, objective: str) -> list[Hypothesis]:
        base = [
            (
                "Event-driven microservices with stream processing for real-time threat detection",
                "Scales ingestion and enables horizontal scaling of analyzers",
                0.72,
                ["scalability", "streaming"],
            ),
            (
                "Modular monolith with async workers — faster to ship, simpler ops for single-tenant",
                "Lower operational complexity; suitable for MVP and regulated environments",
                0.68,
                ["simplicity", "cost"],
            ),
            (
                "Hybrid edge collectors + central analytics lake for distributed telemetry",
                "Balances latency at edge with centralized correlation and ML training",
                0.7,
                ["edge", "hybrid"],
            ),
            (
                "Zero-trust segmentation with policy-as-code and continuous compliance scanning",
                "Addresses security/compliance tradeoffs explicitly",
                0.74,
                ["security", "compliance"],
            ),
        ]
        return [
            Hypothesis(
                id=f"hyp-{uuid.uuid4().hex[:8]}",
                statement=stmt,
                rationale=rat,
                confidence=conf,
                tags=tags,
            )
            for stmt, rat, conf, tags in base
        ]

    @classmethod
    def _generic_hypotheses(cls, objective: str) -> list[Hypothesis]:
        snippet = objective[:80]
        return [
            Hypothesis(
                id=f"hyp-{uuid.uuid4().hex[:8]}",
                statement=f"Sequential deep analysis best addresses: {snippet}",
                rationale="Thorough step-by-step reasoning with tool grounding",
                confidence=0.65,
                tags=["sequential"],
            ),
            Hypothesis(
                id=f"hyp-{uuid.uuid4().hex[:8]}",
                statement=f"Parallel evidence gathering accelerates: {snippet}",
                rationale="Independent agents reduce wall-clock time",
                confidence=0.6,
                tags=["parallel"],
            ),
            Hypothesis(
                id=f"hyp-{uuid.uuid4().hex[:8]}",
                statement="Tool-augmented verification reduces hallucination risk",
                rationale="External sources anchor factual claims",
                confidence=0.7,
                tags=["tools", "verification"],
            ),
        ]

    @classmethod
    def update_status(
        cls,
        hypotheses: list[Hypothesis],
        hypothesis_id: str,
        status: str,
        confidence_delta: float = 0.0,
    ) -> list[Hypothesis]:
        updated: list[Hypothesis] = []
        for h in hypotheses:
            if h.id == hypothesis_id:
                h.status = status
                h.confidence = max(0.0, min(1.0, h.confidence + confidence_delta))
            updated.append(h)
        return updated
