"""
Bounded self-improving runtime — heuristic evolution, prompt tuning, benchmark optimization.

All features are optional via settings. No source-code self-modification.
"""

from __future__ import annotations

__all__ = [
    "SelfImprovementOrchestrator",
]


def __getattr__(name: str):
    if name == "SelfImprovementOrchestrator":
        from app.self_improvement.self_improvement_orchestrator import (
            SelfImprovementOrchestrator,
        )

        return SelfImprovementOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
