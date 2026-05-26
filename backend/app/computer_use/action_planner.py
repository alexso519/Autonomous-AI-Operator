"""
Desktop action planner — multi-step mouse/keyboard/scroll planning.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlannedStep:
    id: str
    action: str
    target: str = ""
    value: str = ""
    coordinates: tuple[int, int] | None = None
    confidence: float = 0.5
    recovery_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "coordinates": list(self.coordinates) if self.coordinates else None,
            "confidence": self.confidence,
            "recoveryHint": self.recovery_hint,
        }


@dataclass
class DesktopPlan:
    objective: str
    steps: list[PlannedStep] = field(default_factory=list)
    mode: str = "desktop"

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "mode": self.mode,
            "steps": [s.to_dict() for s in self.steps],
        }


class ActionPlanner:
    """Plan desktop interactions from natural-language objectives."""

    _CLICK_RE = re.compile(r"\b(click|press|tap|select)\b", re.I)
    _TYPE_RE = re.compile(r"\b(type|enter|input|fill|write)\b", re.I)
    _SCROLL_RE = re.compile(r"\b(scroll|swipe)\b", re.I)
    _OPEN_RE = re.compile(r"\b(open|launch|start)\b", re.I)
    _NAV_RE = re.compile(r"\b(navigate|go to|visit)\b", re.I)

    @classmethod
    def plan(cls, objective: str) -> DesktopPlan:
        lower = objective.lower()
        steps: list[PlannedStep] = []

        steps.append(PlannedStep(
            id=f"step-{uuid.uuid4().hex[:6]}",
            action="observe",
            target="screen",
            confidence=1.0,
            recovery_hint="recapture_screenshot",
        ))

        if cls._NAV_RE.search(lower) or any(k in lower for k in ("http", "www.", ".com")):
            url_match = re.search(r"https?://\S+|www\.\S+", objective)
            target = url_match.group(0) if url_match else "browser"
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="navigate",
                target=target,
                confidence=0.8,
            ))
            mode = "hybrid"
        else:
            mode = "desktop"

        if cls._OPEN_RE.search(lower):
            app = cls._extract_app_name(objective)
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="launch",
                target=app,
                confidence=0.7,
            ))

        if cls._CLICK_RE.search(lower):
            target = cls._extract_click_target(objective)
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="click",
                target=target,
                confidence=0.65,
                recovery_hint="retry_with_grounding",
            ))

        if cls._TYPE_RE.search(lower):
            text = cls._extract_type_text(objective)
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="type",
                target="focused_field",
                value=text,
                confidence=0.6,
            ))

        if cls._SCROLL_RE.search(lower):
            direction = "down" if "down" in lower else "up"
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="scroll",
                target=direction,
                confidence=0.7,
            ))

        if len(steps) == 1:
            steps.append(PlannedStep(
                id=f"step-{uuid.uuid4().hex[:6]}",
                action="analyze",
                target="screen",
                confidence=0.9,
            ))

        steps.append(PlannedStep(
            id=f"step-{uuid.uuid4().hex[:6]}",
            action="validate",
            target="screen",
            confidence=0.8,
            recovery_hint="observe_and_reflect",
        ))

        return DesktopPlan(objective=objective, steps=steps, mode=mode)

    @classmethod
    def _extract_click_target(cls, objective: str) -> str:
        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', objective)
        if quoted:
            return quoted[0][0] or quoted[0][1]
        for word in ("button", "link", "menu", "icon", "tab"):
            idx = objective.lower().find(word)
            if idx >= 0:
                return objective[max(0, idx - 20): idx + len(word) + 20].strip()
        return objective[:80]

    @classmethod
    def _extract_type_text(cls, objective: str) -> str:
        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', objective)
        if quoted:
            return quoted[0][0] or quoted[0][1]
        m = re.search(r"(?:type|enter|input)\s+(.+?)(?:\.|$)", objective, re.I)
        return m.group(1).strip() if m else ""

    @classmethod
    def _extract_app_name(cls, objective: str) -> str:
        m = re.search(r"(?:open|launch|start)\s+(\w+)", objective, re.I)
        return m.group(1) if m else "application"
