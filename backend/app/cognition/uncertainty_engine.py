"""
Uncertainty estimation for deliberative reasoning paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UncertaintyAssessment:
    overall: float
    dimensions: dict[str, float] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    recommendation: str = "proceed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": round(self.overall, 3),
            "dimensions": {k: round(v, 3) for k, v in self.dimensions.items()},
            "signals": self.signals,
            "recommendation": self.recommendation,
        }


class UncertaintyEngine:
    """Rule-based uncertainty scoring — bounded, no open-ended recursion."""

    VAGUE_TERMS = (
        "maybe", "possibly", "might", "unclear", "unknown", "tbd",
        "approximately", "roughly", "unsure",
    )
    HIGH_STAKES = ("security", "compliance", "production", "scalable", "critical")

    @classmethod
    def assess(
        cls,
        objective: str,
        hypotheses: list[dict[str, Any]] | None = None,
        branch_scores: list[float] | None = None,
        prior_uncertainty: float | None = None,
    ) -> UncertaintyAssessment:
        objective_lower = objective.lower()
        signals: list[str] = []
        dimensions: dict[str, float] = {}

        # Objective clarity
        word_count = len(objective.split())
        clarity = 0.3 if word_count < 8 else (0.15 if word_count > 200 else 0.1)
        if "?" in objective:
            clarity += 0.1
            signals.append("objective_contains_questions")
        dimensions["objective_clarity"] = min(1.0, clarity)

        # Vague language
        vague_hits = sum(1 for t in cls.VAGUE_TERMS if t in objective_lower)
        vagueness = min(0.5, vague_hits * 0.08)
        if vague_hits:
            signals.append("vague_language_detected")
        dimensions["linguistic_vagueness"] = vagueness

        # Stakes
        stakes = 0.2 if any(s in objective_lower for s in cls.HIGH_STAKES) else 0.05
        dimensions["decision_stakes"] = stakes

        # Hypothesis spread
        hyp_uncertainty = 0.0
        if hypotheses and len(hypotheses) > 1:
            confidences = [float(h.get("confidence", 0.5)) for h in hypotheses]
            spread = max(confidences) - min(confidences)
            hyp_uncertainty = 0.15 + (0.2 if spread < 0.1 else 0.0)
            if spread < 0.1:
                signals.append("hypothesis_confidence_clustered")
        dimensions["hypothesis_ambiguity"] = hyp_uncertainty

        # Branch disagreement
        branch_unc = 0.0
        if branch_scores and len(branch_scores) > 1:
            top = max(branch_scores)
            second = sorted(branch_scores, reverse=True)[1]
            if top - second < 0.08:
                branch_unc = 0.25
                signals.append("branch_scores_tied")
        dimensions["branch_disagreement"] = branch_unc

        overall = sum(dimensions.values()) / max(len(dimensions), 1)
        if prior_uncertainty is not None:
            overall = (overall * 0.7) + (prior_uncertainty * 0.3)

        overall = min(0.95, max(0.05, overall))

        recommendation = "proceed"
        if overall >= 0.65:
            recommendation = "debate_and_verify"
        elif overall >= 0.45:
            recommendation = "expand_branches"

        return UncertaintyAssessment(
            overall=overall,
            dimensions=dimensions,
            signals=signals,
            recommendation=recommendation,
        )

    @classmethod
    def assess_output(cls, text: str) -> float:
        if not text.strip():
            return 0.9
        unsupported = len(re.findall(r"\b(certainly|definitely|always|never)\b", text, re.I))
        hedges = sum(1 for t in cls.VAGUE_TERMS if t in text.lower())
        length_penalty = 0.2 if len(text) < 80 else 0.0
        return min(0.9, 0.15 + unsupported * 0.05 + hedges * 0.04 + length_penalty)
