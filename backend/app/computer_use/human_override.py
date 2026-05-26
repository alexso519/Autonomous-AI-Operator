"""
Human override — approval escalation and dangerous action classification.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

DANGEROUS_PATTERNS = [
    re.compile(r"\b(delete|remove|format|shutdown|restart|kill|rm\s+-rf)\b", re.I),
    re.compile(r"\b(password|credential|secret|api[_-]?key)\b", re.I),
    re.compile(r"\b(payment|purchase|buy|transfer|wire)\b", re.I),
    re.compile(r"\b(admin|sudo|elevated|registry)\b", re.I),
]

HALLUCINATION_INDICATORS = [
    re.compile(r"\b(impossible|nonexistent|cannot find any)\b", re.I),
]


@dataclass
class ActionClassification:
    action_type: str
    target: str
    danger_level: str = "safe"
    requires_approval: bool = False
    reasons: list[str] = field(default_factory=list)
    hallucination_risk: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionType": self.action_type,
            "target": self.target[:100],
            "dangerLevel": self.danger_level,
            "requiresApproval": self.requires_approval,
            "reasons": self.reasons,
            "hallucinationRisk": round(self.hallucination_risk, 3),
        }


class HumanOverride:
    """Classify dangerous actions and escalate for human approval."""

    _pending_approvals: dict[str, list[ActionClassification]] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def classify(self, action_type: str, target: str, *, context: str = "") -> ActionClassification:
        combined = f"{action_type} {target} {context}"
        reasons: list[str] = []
        danger = "safe"
        requires_approval = False
        hallucination_risk = 0.0

        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(combined):
                reasons.append(f"pattern:{pattern.pattern[:30]}")
                danger = "dangerous"
                requires_approval = True

        if action_type in ("launch",) and any(w in target.lower() for w in ("cmd", "powershell", "terminal")):
            reasons.append("shell_launch")
            if danger == "safe":
                danger = "caution"

        for pattern in HALLUCINATION_INDICATORS:
            if pattern.search(context):
                hallucination_risk = 0.7
                reasons.append("hallucination_indicator")

        if not target and action_type in ("click", "type"):
            hallucination_risk = max(hallucination_risk, 0.5)
            reasons.append("empty_target")

        return ActionClassification(
            action_type=action_type,
            target=target,
            danger_level=danger,
            requires_approval=requires_approval,
            reasons=reasons,
            hallucination_risk=hallucination_risk,
        )

    async def check_and_escalate(
        self,
        action_type: str,
        target: str,
        *,
        context: str = "",
        emit_fn: EmitFn | None = None,
    ) -> ActionClassification:
        classification = self.classify(action_type, target, context=context)

        if classification.requires_approval:
            self._pending_approvals.setdefault(self.execution_id, []).append(classification)
            if emit_fn:
                await emit_fn(
                    self.execution_id,
                    "approval_requested",
                    "SafetyGuard",
                    f"Approval required: {action_type} → {target[:60]}",
                    classification=classification.to_dict(),
                    dangerLevel=classification.danger_level,
                )

        if classification.hallucination_risk > 0.6 and emit_fn:
            await emit_fn(
                self.execution_id,
                "runtime_health_warning",
                "SafetyGuard",
                f"UI hallucination risk: {classification.hallucination_risk:.0%}",
                hallucinationRisk=classification.hallucination_risk,
            )

        return classification

    def pending_count(self) -> int:
        return len(self._pending_approvals.get(self.execution_id, []))

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._pending_approvals.pop(execution_id, None)
