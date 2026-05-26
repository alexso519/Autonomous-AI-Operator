"""
Hard safety limits for bounded self-improvement (no code mutation, capped drift).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SafetyLimits:
    max_heuristic_mutations_per_day: int = 50
    max_prompt_variants: int = 20
    max_parameter_drift: float = 0.15
    max_mutation_rate: float = 0.25
    confidence_threshold: float = 0.75
    benchmark_safety_threshold: float = 0.70
    rollback_trigger_threshold: float = 0.60
    optimization_cooldown_seconds: int = 300


def limits_from_settings() -> SafetyLimits:
    from app.config.settings import settings

    return SafetyLimits(
        max_heuristic_mutations_per_day=settings.max_heuristic_mutations_per_day,
        max_prompt_variants=settings.max_prompt_variants,
        confidence_threshold=settings.self_improvement_confidence_threshold,
    )


def clamp_drift(current: float, proposed: float, max_drift: float) -> float:
    """Bound parameter change to ±max_drift from current."""
    low = max(0.0, current - max_drift)
    high = min(1.0, current + max_drift)
    return max(low, min(high, proposed))


def is_safe_optimization(action: dict[str, Any]) -> bool:
    """Reject optimizations that touch source code or unrestricted recursion."""
    forbidden = {"source_rewrite", "code_patch", "file_write", "recursive_evolution"}
    action_type = str(action.get("type", "")).lower()
    if action_type in forbidden:
        return False
    if action.get("modifiesSourceCode"):
        return False
    if action.get("unboundedRecursion"):
        return False
    return True
