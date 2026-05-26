"""
Attention manager — focus attention on relevant screen regions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class AttentionRegion:
    region_id: str
    bbox: list[int]
    score: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "regionId": self.region_id,
            "bbox": self.bbox,
            "score": round(self.score, 3),
            "reason": self.reason,
        }


class AttentionManager:
    """Focus visual attention on relevant UI regions."""

    _focus_history: dict[str, list[str]] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def compute_attention(
        self,
        elements: list[dict[str, Any]],
        *,
        predictions: list[dict[str, Any]] | None = None,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> list[AttentionRegion]:
        regions: list[AttentionRegion] = []
        pred_targets = {p.get("target", "").lower() for p in (predictions or [])}

        for i, el in enumerate(elements):
            bbox = el.get("bbox", el.get("bounds", []))
            if len(bbox) < 4:
                continue
            label = str(el.get("label", "")).lower()
            score = 0.3
            reason = "baseline"

            if el.get("interactive"):
                score += 0.2
                reason = "interactive"
            if el.get("semanticType") in ("dialog", "button"):
                score += 0.25
                reason = "actionable"
            if label in pred_targets or any(t in label for t in pred_targets if t):
                score += 0.3
                reason = "predicted_target"
            if el.get("semanticType") == "dialog":
                score += 0.15
                reason = "dialog_focus"

            regions.append(AttentionRegion(
                region_id=f"att-{i}",
                bbox=bbox,
                score=min(score, 1.0),
                reason=reason,
            ))

        regions.sort(key=lambda r: -r.score)
        return regions[:10]

    async def shift_attention(
        self,
        elements: list[dict[str, Any]],
        *,
        predictions: list[dict[str, Any]] | None = None,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> list[AttentionRegion]:
        regions = self.compute_attention(elements, predictions=predictions)
        if regions:
            top = regions[0]
            history = self._focus_history.setdefault(self.execution_id, [])
            if top.region_id not in history[-3:]:
                history.append(top.region_id)
                if emit_fn:
                    await emit_fn(
                        self.execution_id,
                        "attention_shifted",
                        agent,
                        f"Attention → {top.reason} (score {top.score:.0%})",
                        region=top.to_dict(),
                        allRegions=[r.to_dict() for r in regions[:5]],
                    )
        return regions

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._focus_history.pop(execution_id, None)
