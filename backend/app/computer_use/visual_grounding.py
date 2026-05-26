"""
Visual grounding — map natural-language targets to screen coordinates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.computer_use.ui_detector import UIElement
from app.computer_use.visual_memory import VisualMemory

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class GroundedTarget:
    target_id: str
    label: str
    x: int
    y: int
    width: int
    height: int
    confidence: float
    element_type: str = "unknown"
    reasoning: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_dict(self) -> dict[str, Any]:
        cx, cy = self.center
        return {
            "targetId": self.target_id,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "centerX": cx,
            "centerY": cy,
            "confidence": self.confidence,
            "elementType": self.element_type,
            "reasoning": self.reasoning,
        }


class VisualGrounding:
    """Ground text queries to screen coordinates with confidence scoring."""

    def __init__(self, memory: VisualMemory) -> None:
        self.memory = memory

    def ground(self, query: str) -> GroundedTarget | None:
        latest = self.memory.latest
        if not latest or not latest.elements:
            return None

        q = query.lower().strip()
        best: UIElement | None = None
        best_score = 0.0
        reasoning = ""

        for el in latest.elements:
            label = el.label.lower()
            score = 0.0
            if q == label:
                score = 1.0
                reasoning = "exact_label_match"
            elif q in label:
                score = 0.85 + el.confidence * 0.1
                reasoning = "substring_match"
            elif label in q:
                score = 0.7 + el.confidence * 0.1
                reasoning = "label_in_query"
            else:
                q_tokens = set(q.split())
                l_tokens = set(label.split())
                overlap = len(q_tokens & l_tokens)
                if overlap and q_tokens:
                    score = 0.4 + (overlap / len(q_tokens)) * 0.4
                    reasoning = "token_overlap"

            if el.interactive:
                score += 0.05
            if score > best_score:
                best_score = score
                best = el

        if best is None or best_score < 0.35:
            return None

        return GroundedTarget(
            target_id=f"gt-{uuid.uuid4().hex[:8]}",
            label=best.label,
            x=best.x,
            y=best.y,
            width=best.width,
            height=best.height,
            confidence=min(best_score, 1.0),
            element_type=best.element_type,
            reasoning=reasoning,
        )

    async def ground_and_emit(
        self,
        execution_id: str,
        query: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> GroundedTarget | None:
        target = self.ground(query)
        if target and emit_fn:
            await emit_fn(
                execution_id,
                "visual_grounding_completed",
                agent,
                f"Grounded '{query}' → ({target.center[0]}, {target.center[1]})",
                **target.to_dict(),
                query=query,
            )
        return target

    def score_click_confidence(self, target: GroundedTarget) -> float:
        base = target.confidence
        if target.element_type in ("button", "link", "menu"):
            base += 0.1
        if target.width < 5 or target.height < 5:
            base -= 0.2
        return max(0.0, min(1.0, base))
