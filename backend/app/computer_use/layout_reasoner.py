"""
Layout reasoner — infer spatial structure and workflow context from UI layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class LayoutRegion:
    region_id: str
    region_type: str
    bbox: list[int] = field(default_factory=list)
    element_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "regionId": self.region_id,
            "regionType": self.region_type,
            "bbox": self.bbox,
            "elementIds": self.element_ids,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class LayoutAnalysis:
    regions: list[LayoutRegion] = field(default_factory=list)
    reading_order: list[str] = field(default_factory=list)
    primary_action: str = ""
    layout_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "regions": [r.to_dict() for r in self.regions],
            "readingOrder": self.reading_order,
            "primaryAction": self.primary_action,
            "layoutType": self.layout_type,
        }


class LayoutReasoner:
    """Reason about spatial UI layout from semantic elements."""

    def reason(
        self,
        elements: list[dict[str, Any]],
        *,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> LayoutAnalysis:
        if not elements:
            return LayoutAnalysis(layout_type="empty")

        regions = self._cluster_regions(elements, screen_width, screen_height)
        reading_order = self._reading_order(elements)
        primary = self._primary_action(elements)
        layout_type = self._classify_layout(regions)

        return LayoutAnalysis(
            regions=regions,
            reading_order=reading_order,
            primary_action=primary,
            layout_type=layout_type,
        )

    async def reason_and_emit(
        self,
        execution_id: str,
        elements: list[dict[str, Any]],
        *,
        screen_width: int = 1920,
        screen_height: int = 1080,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> LayoutAnalysis:
        analysis = self.reason(elements, screen_width=screen_width, screen_height=screen_height)
        if emit_fn:
            await emit_fn(
                execution_id,
                "layout_reasoned",
                agent,
                f"Layout: {analysis.layout_type} ({len(analysis.regions)} regions)",
                layout=analysis.to_dict(),
            )
        return analysis

    def _cluster_regions(
        self,
        elements: list[dict[str, Any]],
        width: int,
        height: int,
    ) -> list[LayoutRegion]:
        top: list[str] = []
        center: list[str] = []
        bottom: list[str] = []
        sidebar: list[str] = []

        for el in elements:
            bbox = el.get("bbox", el.get("bounds", []))
            if len(bbox) < 4:
                continue
            eid = el.get("elementId", el.get("id", ""))
            cy = (bbox[1] + bbox[3]) / 2
            cx = (bbox[0] + bbox[2]) / 2
            if cy < height * 0.15:
                top.append(eid)
            elif cy > height * 0.85:
                bottom.append(eid)
            elif cx < width * 0.2:
                sidebar.append(eid)
            else:
                center.append(eid)

        regions: list[LayoutRegion] = []
        if top:
            regions.append(LayoutRegion("reg-top", "toolbar", element_ids=top, confidence=0.7))
        if center:
            regions.append(LayoutRegion("reg-center", "content", element_ids=center, confidence=0.8))
        if bottom:
            regions.append(LayoutRegion("reg-bottom", "statusbar", element_ids=bottom, confidence=0.6))
        if sidebar:
            regions.append(LayoutRegion("reg-sidebar", "sidebar", element_ids=sidebar, confidence=0.65))
        return regions

    def _reading_order(self, elements: list[dict[str, Any]]) -> list[str]:
        def sort_key(el: dict[str, Any]) -> tuple[float, float]:
            bbox = el.get("bbox", el.get("bounds", [0, 0, 0, 0]))
            return (bbox[1] if len(bbox) > 1 else 0, bbox[0] if bbox else 0)

        sorted_els = sorted(elements, key=sort_key)
        return [el.get("elementId", el.get("id", "")) for el in sorted_els[:20]]

    def _primary_action(self, elements: list[dict[str, Any]]) -> str:
        for el in elements:
            sem = el.get("semanticType", el.get("semantic_type", ""))
            label = str(el.get("label", "")).lower()
            if sem == "button" and any(w in label for w in ("ok", "submit", "save", "next", "continue")):
                return el.get("label", "")
        for el in elements:
            if el.get("semanticType") == "button" or el.get("interactive"):
                return el.get("label", "")
        return ""

    def _classify_layout(self, regions: list[LayoutRegion]) -> str:
        types = {r.region_type for r in regions}
        if "sidebar" in types and "content" in types:
            return "sidebar_content"
        if "toolbar" in types and "content" in types:
            return "standard_app"
        if len(regions) == 1:
            return regions[0].region_type
        return "complex"
