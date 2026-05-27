"""
Confidence-aware execution: scores agent outputs and triggers recovery actions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.config.locale import t
from app.execution.context_manager import ContextManager
from app.execution.execution_quality import ExecutionQualityScorer


class ConfidenceAction(str, Enum):
    NONE = "none"
    TOOL_GROUNDING = "tool_grounding"
    CLARIFICATION_RETRY = "clarification_retry"
    ALTERNATE_REASONING = "alternate_reasoning"
    SYNTHESIS_FALLBACK = "synthesis_fallback"


@dataclass
class ConfidenceAssessment:
    node_id: str
    agent_name: str
    score: float
    factuality: float
    completion: float
    triggers: list[str]
    recommended_action: ConfidenceAction
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "agentName": self.agent_name,
            "score": round(self.score, 3),
            "factuality": round(self.factuality, 3),
            "completion": round(self.completion, 3),
            "triggers": self.triggers,
            "recommendedAction": self.recommended_action.value,
            "reasoning": self.reasoning,
        }


class ConfidenceEngine:
    """Derive confidence from quality scores and output signals."""

    LOW_THRESHOLD = 0.55
    CRITICAL_THRESHOLD = 0.35

    HEDGE_PATTERN = re.compile(
        r"\b(might|could|possibly|perhaps|uncertain|not sure|i think)\b",
        re.IGNORECASE,
    )

    @classmethod
    def assess(
        cls,
        node: dict[str, Any],
        ctx: ContextManager,
    ) -> ConfidenceAssessment:
        node_id = node.get("id", "unknown")
        agent_name = node.get("data", {}).get("label", "Agent")
        output = ctx.get_all_outputs().get(node_id, "").strip()

        quality = ExecutionQualityScorer.score_output(node, ctx)
        factuality = quality.factuality_confidence
        completion = quality.completion_confidence
        hallucination_penalty = quality.hallucination_risk * 0.25

        hedge_penalty = 0.0
        if output and cls.HEDGE_PATTERN.search(output):
            hedge_penalty = 0.08

        tool_bonus = 0.0
        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        if isinstance(tool_calls, list) and tool_calls:
            completed = [t for t in tool_calls if t.get("status") == "completed"]
            if completed:
                tool_bonus = min(0.12, len(completed) * 0.03)

        score = max(
            0.0,
            min(
                1.0,
                (factuality * 0.45 + completion * 0.35 + quality.overall_score * 0.2)
                - hallucination_penalty
                - hedge_penalty
                + tool_bonus,
            ),
        )

        triggers: list[str] = []
        action = ConfidenceAction.NONE
        reasoning_parts: list[str] = []

        if score < cls.CRITICAL_THRESHOLD:
            triggers.append("critical_low_confidence")
            action = ConfidenceAction.SYNTHESIS_FALLBACK
            reasoning_parts.append(t("confidence_critical"))
        elif score < cls.LOW_THRESHOLD:
            triggers.append("low_confidence")
            if "unsupported_claims" in quality.issues or quality.hallucination_risk > 0.5:
                action = ConfidenceAction.TOOL_GROUNDING
                triggers.append("needs_grounding")
                reasoning_parts.append(t("confidence_low_grounding"))
            elif quality.completion_confidence < 0.5:
                action = ConfidenceAction.CLARIFICATION_RETRY
                reasoning_parts.append(t("confidence_incomplete"))
            else:
                action = ConfidenceAction.ALTERNATE_REASONING
                reasoning_parts.append(t("confidence_low_alternate"))

        if not output or len(output.split()) < 40:
            triggers.append("thin_output")
            if action == ConfidenceAction.NONE:
                action = ConfidenceAction.CLARIFICATION_RETRY

        if not reasoning_parts:
            reasoning_parts.append(t("confidence_ok", score=f"{score:.0%}"))

        assessment = ConfidenceAssessment(
            node_id=node_id,
            agent_name=agent_name,
            score=score,
            factuality=factuality,
            completion=completion,
            triggers=triggers,
            recommended_action=action,
            reasoning=" ".join(reasoning_parts),
        )

        history = ctx.get_workflow_memory("confidence_history") or []
        history.append(assessment.to_dict())
        ctx.set_workflow_memory("confidence_history", history[-40:])
        ctx.set_workflow_memory("last_confidence", assessment.to_dict())
        ctx.add_node_memory(node_id, "confidence", assessment.to_dict())

        if action != ConfidenceAction.NONE:
            ctx.set_workflow_memory(
                "pending_confidence_action",
                {
                    "nodeId": node_id,
                    "action": action.value,
                    "score": score,
                },
            )

        return assessment

    @classmethod
    def should_force_tool_grounding(cls, ctx: ContextManager) -> bool:
        pending = ctx.get_workflow_memory("pending_confidence_action") or {}
        return pending.get("action") == ConfidenceAction.TOOL_GROUNDING.value
