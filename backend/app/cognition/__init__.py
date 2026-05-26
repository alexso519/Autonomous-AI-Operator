"""
Cognitive architecture — deliberative reasoning, collaboration, and self-improvement.

Bounded, inspectable cognition layered on the existing execution runtime.
"""

__all__ = ["CognitiveOrchestrator"]


def __getattr__(name: str):
    if name == "CognitiveOrchestrator":
        from app.cognition.cognitive_orchestrator import CognitiveOrchestrator
        return CognitiveOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
