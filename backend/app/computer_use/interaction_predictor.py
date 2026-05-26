"""
Interaction predictor — infer likely next actions from UI state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class PredictedAction:
    action_type: str
    target: str
    confidence: float
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionType": self.action_type,
            "target": self.target,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
        }


@dataclass
class InteractionPrediction:
    predictions: list[PredictedAction] = field(default_factory=list)
    workflow_class: str = "unknown"
    similarity_score: float = 0.0
    matched_pattern_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "predictions": [p.to_dict() for p in self.predictions],
            "workflowClass": self.workflow_class,
            "similarityScore": round(self.similarity_score, 3),
            "matchedPatternId": self.matched_pattern_id,
        }


class InteractionPredictor:
    """Predict next interactions from semantic UI and pattern memory."""

    def predict(
        self,
        semantic_ui: dict[str, Any],
        layout: dict[str, Any] | None = None,
        *,
        semantic_memory: Any = None,
    ) -> InteractionPrediction:
        workflow = semantic_ui.get("workflowHint", "exploration")
        dominant = semantic_ui.get("dominantType", "unknown")
        elements = semantic_ui.get("elements", [])
        predictions: list[PredictedAction] = []

        primary = (layout or {}).get("primaryAction", "")
        if primary:
            predictions.append(PredictedAction(
                action_type="click",
                target=primary,
                confidence=0.75,
                rationale="Primary action from layout analysis",
            ))

        for el in elements:
            sem = el.get("semanticType", "")
            label = el.get("label", "")
            if sem == "dialog" and label:
                predictions.append(PredictedAction(
                    action_type="click",
                    target=label,
                    confidence=0.8,
                    rationale="Dialog requires response",
                ))
            elif sem == "input" and label:
                predictions.append(PredictedAction(
                    action_type="type",
                    target=label,
                    confidence=0.65,
                    rationale="Form input field detected",
                ))
            elif sem == "menu" and label:
                predictions.append(PredictedAction(
                    action_type="click",
                    target=label,
                    confidence=0.6,
                    rationale="Menu navigation option",
                ))

        similarity_score = 0.0
        matched_pattern = ""
        if semantic_memory:
            labels = [e.get("label", "") for e in elements]
            similar = semantic_memory.find_similar(dominant, labels, len(elements))
            if similar:
                matched_pattern = similar[0][0].pattern_id
                similarity_score = similar[0][1]

        predictions.sort(key=lambda p: -p.confidence)
        return InteractionPrediction(
            predictions=predictions[:5],
            workflow_class=workflow,
            similarity_score=similarity_score,
            matched_pattern_id=matched_pattern,
        )

    async def predict_and_emit(
        self,
        execution_id: str,
        semantic_ui: dict[str, Any],
        layout: dict[str, Any] | None = None,
        *,
        semantic_memory: Any = None,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> InteractionPrediction:
        result = self.predict(semantic_ui, layout, semantic_memory=semantic_memory)

        if emit_fn:
            await emit_fn(
                execution_id,
                "interaction_predicted",
                agent,
                f"Predicted {len(result.predictions)} action(s) ({result.workflow_class})",
                prediction=result.to_dict(),
            )
            if result.similarity_score > 0.5:
                await emit_fn(
                    execution_id,
                    "ui_similarity_detected",
                    agent,
                    f"UI similarity {result.similarity_score:.0%} (pattern {result.matched_pattern_id})",
                    similarityScore=result.similarity_score,
                    matchedPatternId=result.matched_pattern_id,
                )
        return result
