"""
Multimodal reasoner — combine OCR, layout, and interaction history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class MultimodalContext:
    ocr_summary: str = ""
    layout_type: str = ""
    semantic_dominant: str = ""
    interaction_history: list[dict[str, Any]] = field(default_factory=list)
    attention_regions: list[dict[str, Any]] = field(default_factory=list)
    stall_detected: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ocrSummary": self.ocr_summary[:300],
            "layoutType": self.layout_type,
            "semanticDominant": self.semantic_dominant,
            "interactionCount": len(self.interaction_history),
            "attentionRegions": self.attention_regions,
            "stallDetected": self.stall_detected,
            "confidence": round(self.confidence, 3),
        }


class MultimodalReasoner:
    """Fuse perception modalities into unified desktop context."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def reason(
        self,
        *,
        ocr_text: str = "",
        semantic_ui: dict[str, Any] | None = None,
        layout: dict[str, Any] | None = None,
        interaction_history: list[dict[str, Any]] | None = None,
        attention: list[dict[str, Any]] | None = None,
        screen_changes: dict[str, Any] | None = None,
    ) -> MultimodalContext:
        sem = semantic_ui or {}
        lay = layout or {}
        changes = screen_changes or {}

        stall = changes.get("stallDetected", False) or changes.get("changeRatio", 1.0) < 0.01
        history = interaction_history or []

        confidence = 0.5
        if ocr_text:
            confidence += 0.15
        if sem.get("elementCount", 0) > 0:
            confidence += 0.15
        if lay.get("layoutType"):
            confidence += 0.1
        if history:
            confidence += 0.1

        return MultimodalContext(
            ocr_summary=ocr_text[:500],
            layout_type=lay.get("layoutType", ""),
            semantic_dominant=sem.get("dominantType", ""),
            interaction_history=history[-10:],
            attention_regions=attention or [],
            stall_detected=stall,
            confidence=min(confidence, 1.0),
        )

    async def reason_and_emit(
        self,
        *,
        ocr_text: str = "",
        semantic_ui: dict[str, Any] | None = None,
        layout: dict[str, Any] | None = None,
        interaction_history: list[dict[str, Any]] | None = None,
        attention: list[dict[str, Any]] | None = None,
        screen_changes: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> MultimodalContext:
        ctx = self.reason(
            ocr_text=ocr_text,
            semantic_ui=semantic_ui,
            layout=layout,
            interaction_history=interaction_history,
            attention=attention,
            screen_changes=screen_changes,
        )
        if emit_fn:
            await emit_fn(
                self.execution_id,
                "multimodal_context_updated",
                agent,
                f"Multimodal context: {ctx.layout_type}/{ctx.semantic_dominant}",
                context=ctx.to_dict(),
            )
        return ctx
