"""
Semantic UI parser — classify desktop UI regions into semantic categories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]

SEMANTIC_TYPES = frozenset({
    "menu", "form", "dialog", "toolbar", "table", "button", "input",
    "tab", "sidebar", "statusbar", "list", "panel", "unknown",
})

_MENU_PATTERNS = re.compile(r"\b(file|edit|view|help|tools|settings|menu)\b", re.I)
_DIALOG_PATTERNS = re.compile(r"\b(ok|cancel|close|confirm|alert|dialog|warning|error)\b", re.I)
_TOOLBAR_PATTERNS = re.compile(r"\b(save|undo|redo|copy|paste|bold|italic|format)\b", re.I)
_FORM_PATTERNS = re.compile(r"\b(name|email|password|username|submit|login|sign in)\b", re.I)
_TABLE_PATTERNS = re.compile(r"\b(row|column|header|sort|filter|table)\b", re.I)


@dataclass
class SemanticElement:
    element_id: str
    semantic_type: str
    label: str
    bbox: list[int] = field(default_factory=list)
    confidence: float = 0.0
    interactive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "elementId": self.element_id,
            "semanticType": self.semantic_type,
            "label": self.label,
            "bbox": self.bbox,
            "confidence": round(self.confidence, 3),
            "interactive": self.interactive,
            "metadata": self.metadata,
        }


@dataclass
class SemanticUIParse:
    elements: list[SemanticElement] = field(default_factory=list)
    layout_summary: str = ""
    workflow_hint: str = ""
    dominant_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": [e.to_dict() for e in self.elements],
            "layoutSummary": self.layout_summary,
            "workflowHint": self.workflow_hint,
            "dominantType": self.dominant_type,
            "elementCount": len(self.elements),
        }


class UISemanticParser:
    """Parse raw UI elements into semantic categories."""

    def classify_label(self, label: str, element_type: str = "unknown") -> str:
        text = label.lower().strip()
        if not text:
            return element_type if element_type in SEMANTIC_TYPES else "unknown"
        if _DIALOG_PATTERNS.search(text):
            return "dialog"
        if _MENU_PATTERNS.search(text):
            return "menu"
        if _TOOLBAR_PATTERNS.search(text):
            return "toolbar"
        if _FORM_PATTERNS.search(text):
            return "form"
        if _TABLE_PATTERNS.search(text):
            return "table"
        if element_type in ("button", "input", "link"):
            return element_type
        if len(text) < 20 and text.isupper():
            return "toolbar"
        return element_type if element_type in SEMANTIC_TYPES else "unknown"

    def parse(
        self,
        elements: list[dict[str, Any]],
        *,
        ocr_text: str = "",
    ) -> SemanticUIParse:
        semantic_elements: list[SemanticElement] = []
        type_counts: dict[str, int] = {}

        for i, el in enumerate(elements):
            label = el.get("label", el.get("text", ""))
            raw_type = el.get("elementType", el.get("type", "unknown"))
            sem_type = self.classify_label(str(label), str(raw_type))
            type_counts[sem_type] = type_counts.get(sem_type, 0) + 1
            semantic_elements.append(SemanticElement(
                element_id=el.get("id", f"sem-{i}"),
                semantic_type=sem_type,
                label=str(label),
                bbox=el.get("bbox", el.get("bounds", [])),
                confidence=float(el.get("confidence", 0.5)),
                interactive=bool(el.get("interactive", sem_type in ("button", "input", "menu"))),
            ))

        dominant = max(type_counts, key=type_counts.get) if type_counts else "unknown"
        summary = self._build_summary(type_counts, ocr_text)
        hint = self._infer_workflow(dominant, semantic_elements)

        return SemanticUIParse(
            elements=semantic_elements,
            layout_summary=summary,
            workflow_hint=hint,
            dominant_type=dominant,
        )

    async def parse_and_emit(
        self,
        execution_id: str,
        elements: list[dict[str, Any]],
        *,
        ocr_text: str = "",
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> SemanticUIParse:
        result = self.parse(elements, ocr_text=ocr_text)
        if emit_fn:
            await emit_fn(
                execution_id,
                "semantic_ui_parsed",
                agent,
                f"Parsed {len(result.elements)} semantic elements ({result.dominant_type})",
                semanticUi=result.to_dict(),
            )
        return result

    def _build_summary(self, type_counts: dict[str, int], ocr_text: str) -> str:
        parts = [f"{k}:{v}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1])]
        summary = f"Layout types: {', '.join(parts[:5])}"
        if ocr_text:
            summary += f"; OCR excerpt: {ocr_text[:80]}"
        return summary

    def _infer_workflow(self, dominant: str, elements: list[SemanticElement]) -> str:
        hints = {
            "form": "data_entry",
            "dialog": "confirmation",
            "menu": "navigation",
            "toolbar": "editing",
            "table": "data_browsing",
        }
        base = hints.get(dominant, "exploration")
        if any(e.semantic_type == "dialog" for e in elements):
            return "confirmation_pending"
        return base
