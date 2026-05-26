"""
UI component detection from OCR regions and heuristics.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.computer_use.ocr_engine import OCRRegion, OCRResult

INTERACTIVE_PATTERNS = (
    re.compile(r"^(ok|cancel|submit|save|open|close|next|back|login|sign in)$", re.I),
    re.compile(r"^(file|edit|view|help|settings|menu)$", re.I),
    re.compile(r"button|click|link", re.I),
)

ELEMENT_TYPES = {
    "button": re.compile(r"^(ok|cancel|submit|save|open|close|next|back|login|sign in|apply)$", re.I),
    "menu": re.compile(r"^(file|edit|view|help|settings|menu|tools)$", re.I),
    "input": re.compile(r"^(search|username|password|email|name|address)$", re.I),
    "link": re.compile(r"^(http|www\.|\.com|\.org)", re.I),
}


@dataclass
class UIElement:
    id: str
    element_type: str
    label: str
    x: int
    y: int
    width: int
    height: int
    confidence: float = 0.0
    interactive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "elementType": self.element_type,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "interactive": self.interactive,
            "centerX": self.center[0],
            "centerY": self.center[1],
            "metadata": self.metadata,
        }


class UIDetector:
    """Detect UI components from OCR regions with bounding-box extraction."""

    @classmethod
    def detect_from_ocr(cls, ocr: OCRResult) -> list[UIElement]:
        elements: list[UIElement] = []
        for region in ocr.regions:
            el = cls._region_to_element(region)
            if el:
                elements.append(el)
        return cls._merge_nearby(elements)

    @classmethod
    def _region_to_element(cls, region: OCRRegion) -> UIElement | None:
        text = region.text.strip()
        if not text or len(text) < 2:
            return None
        element_type = cls._classify(text)
        interactive = element_type in ("button", "menu", "input", "link") or cls._is_interactive(text)
        confidence = region.confidence if region.confidence > 0 else (0.6 if interactive else 0.3)
        return UIElement(
            id=f"ui-{uuid.uuid4().hex[:8]}",
            element_type=element_type,
            label=text,
            x=region.x,
            y=region.y,
            width=max(region.width, 1),
            height=max(region.height, 1),
            confidence=confidence,
            interactive=interactive,
        )

    @classmethod
    def _classify(cls, text: str) -> str:
        for etype, pattern in ELEMENT_TYPES.items():
            if pattern.search(text):
                return etype
        if len(text) > 40:
            return "text"
        return "label"

    @classmethod
    def _is_interactive(cls, text: str) -> bool:
        return any(p.search(text) for p in INTERACTIVE_PATTERNS)

    @classmethod
    def _merge_nearby(cls, elements: list[UIElement], threshold: int = 8) -> list[UIElement]:
        if len(elements) <= 1:
            return elements
        merged: list[UIElement] = []
        used: set[str] = set()
        for i, a in enumerate(elements):
            if a.id in used:
                continue
            group = [a]
            used.add(a.id)
            for b in elements[i + 1:]:
                if b.id in used:
                    continue
                if abs(a.x - b.x) <= threshold and abs(a.y - b.y) <= threshold:
                    group.append(b)
                    used.add(b.id)
            if len(group) == 1:
                merged.append(a)
            else:
                labels = " ".join(g.label for g in group)
                xs = [g.x for g in group]
                ys = [g.y for g in group]
                merged.append(UIElement(
                    id=f"ui-{uuid.uuid4().hex[:8]}",
                    element_type=group[0].element_type,
                    label=labels[:120],
                    x=min(xs),
                    y=min(ys),
                    width=max(g.x + g.width for g in group) - min(xs),
                    height=max(g.y + g.height for g in group) - min(ys),
                    confidence=max(g.confidence for g in group),
                    interactive=any(g.interactive for g in group),
                ))
        return merged

    @classmethod
    def find_by_label(cls, elements: list[UIElement], query: str) -> UIElement | None:
        q = query.lower()
        best: UIElement | None = None
        best_score = 0.0
        for el in elements:
            label = el.label.lower()
            if q in label or label in q:
                score = el.confidence + (0.3 if el.interactive else 0.0)
                if score > best_score:
                    best_score = score
                    best = el
        return best
