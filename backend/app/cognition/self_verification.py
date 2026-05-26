"""
Self-verification loops for deliberative outputs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.cognition.uncertainty_engine import UncertaintyEngine


@dataclass
class VerificationResult:
    id: str
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    confidence_after: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "checks": self.checks,
            "failureReasons": self.failure_reasons,
            "confidenceAfter": round(self.confidence_after, 3),
            "createdAt": self.created_at,
        }


class SelfVerification:
    """Bounded verification — no recursive self-modification."""

    MIN_CONSENSUS_LENGTH = 40
    REQUIRED_TRADEOFF_KEYWORDS = ("cost", "performance", "security", "risk", "trade")

    @classmethod
    def verify_consensus(
        cls,
        synthesis: str,
        objective: str,
        min_confidence: float = 0.5,
    ) -> VerificationResult:
        checks: list[dict[str, Any]] = []
        failures: list[str] = []

        # Length check
        length_ok = len(synthesis.strip()) >= cls.MIN_CONSENSUS_LENGTH
        checks.append({"name": "synthesis_length", "passed": length_ok})
        if not length_ok:
            failures.append("consensus_too_short")

        # Objective alignment (keyword overlap)
        obj_tokens = set(objective.lower().split()) - {"a", "the", "and", "for", "to", "of"}
        syn_tokens = set(synthesis.lower().split())
        overlap = len(obj_tokens & syn_tokens) / max(len(obj_tokens), 1)
        align_ok = overlap >= 0.15 or len(objective.split()) < 6
        checks.append(
            {"name": "objective_alignment", "passed": align_ok, "overlap": round(overlap, 3)}
        )
        if not align_ok:
            failures.append("weak_objective_alignment")

        # Architecture tasks should mention tradeoffs
        is_design = any(
            k in objective.lower()
            for k in ("design", "architecture", "platform", "scalable", "monitoring")
        )
        if is_design:
            tradeoff_ok = any(
                k in synthesis.lower() for k in cls.REQUIRED_TRADEOFF_KEYWORDS
            )
            checks.append({"name": "tradeoff_coverage", "passed": tradeoff_ok})
            if not tradeoff_ok:
                failures.append("missing_tradeoff_analysis")

        # Output uncertainty
        output_uncertainty = UncertaintyEngine.assess_output(synthesis)
        uncertainty_ok = output_uncertainty < 0.55
        checks.append(
            {
                "name": "output_certainty",
                "passed": uncertainty_ok,
                "score": round(output_uncertainty, 3),
            }
        )
        if not uncertainty_ok:
            failures.append("high_output_uncertainty")

        passed = len(failures) == 0
        confidence = 0.85 if passed else max(0.2, 0.6 - len(failures) * 0.15)

        return VerificationResult(
            id=f"ver-{uuid.uuid4().hex[:8]}",
            passed=passed,
            checks=checks,
            failure_reasons=failures,
            confidence_after=confidence,
        )

    @classmethod
    def verify_branch(cls, branch: dict[str, Any]) -> VerificationResult:
        label = branch.get("label", "")
        score = float(branch.get("score", 0))
        checks = [
            {"name": "has_label", "passed": bool(label)},
            {"name": "score_threshold", "passed": score >= 0.35, "score": score},
        ]
        failures = []
        if not label:
            failures.append("empty_branch_label")
        if score < 0.35:
            failures.append("low_branch_score")

        return VerificationResult(
            id=f"ver-{uuid.uuid4().hex[:8]}",
            passed=len(failures) == 0,
            checks=checks,
            failure_reasons=failures,
            confidence_after=score if score else 0.4,
        )
