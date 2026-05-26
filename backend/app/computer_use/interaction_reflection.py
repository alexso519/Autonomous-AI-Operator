"""
Interaction reflection — analyze failed UI actions and suggest recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReflectionResult:
    should_retry: bool
    recovery_strategy: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    adjusted_target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shouldRetry": self.should_retry,
            "recoveryStrategy": self.recovery_strategy,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "adjustedTarget": self.adjusted_target,
        }


class InteractionReflection:
    """Reflect on failed interactions and propose recovery strategies."""

    MAX_FAILURES_BEFORE_ESCALATE = 3

    def __init__(self) -> None:
        self._failure_counts: dict[str, int] = {}

    def reflect(
        self,
        action_type: str,
        target: str,
        error: str,
        *,
        attempt: int = 1,
    ) -> ReflectionResult:
        key = f"{action_type}:{target}"
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
        count = self._failure_counts[key]
        reasons: list[str] = [f"attempt_{attempt}", error or "unknown_error"]

        if count >= self.MAX_FAILURES_BEFORE_ESCALATE:
            return ReflectionResult(
                should_retry=False,
                recovery_strategy="escalate_to_human",
                confidence=0.2,
                reasons=reasons + ["max_failures_reached"],
            )

        strategy = "recapture_and_reground"
        if "restricted" in error or "dangerous" in error:
            return ReflectionResult(
                should_retry=False,
                recovery_strategy="abort",
                confidence=0.0,
                reasons=reasons + ["safety_blocked"],
            )
        if "rate_limit" in error:
            strategy = "wait_and_retry"
        elif "grounding" in error:
            strategy = "recapture_and_reground"
        elif action_type == "click":
            strategy = "retry_with_offset"
        elif action_type == "type":
            strategy = "refocus_and_retype"

        return ReflectionResult(
            should_retry=True,
            recovery_strategy=strategy,
            confidence=max(0.3, 0.8 - count * 0.15),
            reasons=reasons,
            adjusted_target=target,
        )

    def reset(self, action_type: str, target: str) -> None:
        key = f"{action_type}:{target}"
        self._failure_counts.pop(key, None)
