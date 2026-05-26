"""
Screen change detector — compare sequential screenshots for dynamic changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class ScreenChange:
    change_detected: bool = False
    change_ratio: float = 0.0
    stall_detected: bool = False
    frames_since_change: int = 0
    previous_hash: str = ""
    current_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeDetected": self.change_detected,
            "changeRatio": round(self.change_ratio, 4),
            "stallDetected": self.stall_detected,
            "framesSinceChange": self.frames_since_change,
            "previousHash": self.previous_hash[:16],
            "currentHash": self.current_hash[:16],
        }


class ScreenChangeDetector:
    """Detect dynamic screen changes and stalled workflows."""

    _last_hash: dict[str, str] = {}
    _frames_still: dict[str, int] = {}
    STALL_THRESHOLD = 5

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    @staticmethod
    def hash_content(text: str, element_count: int = 0) -> str:
        key = f"{text[:500]}:{element_count}"
        return hashlib.sha256(key.encode()).hexdigest()

    def detect(
        self,
        *,
        ocr_text: str = "",
        element_count: int = 0,
        fingerprint: str = "",
    ) -> ScreenChange:
        current = fingerprint or self.hash_content(ocr_text, element_count)
        previous = self._last_hash.get(self.execution_id, "")

        change_detected = bool(previous and previous != current)
        change_ratio = 0.0 if not previous else (1.0 if change_detected else 0.0)

        if change_detected:
            self._frames_still[self.execution_id] = 0
        else:
            self._frames_still[self.execution_id] = self._frames_still.get(self.execution_id, 0) + 1

        stall = self._frames_still.get(self.execution_id, 0) >= self.STALL_THRESHOLD
        self._last_hash[self.execution_id] = current

        return ScreenChange(
            change_detected=change_detected,
            change_ratio=change_ratio,
            stall_detected=stall and bool(previous),
            frames_since_change=self._frames_still.get(self.execution_id, 0),
            previous_hash=previous,
            current_hash=current,
        )

    async def detect_and_emit(
        self,
        *,
        ocr_text: str = "",
        element_count: int = 0,
        fingerprint: str = "",
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> ScreenChange:
        change = self.detect(ocr_text=ocr_text, element_count=element_count, fingerprint=fingerprint)

        if emit_fn:
            if change.change_detected:
                await emit_fn(
                    self.execution_id,
                    "screen_change_detected",
                    agent,
                    f"Screen changed (ratio {change.change_ratio:.0%})",
                    change=change.to_dict(),
                )
            if change.stall_detected:
                await emit_fn(
                    self.execution_id,
                    "visual_stall_detected",
                    agent,
                    f"Visual stall: {change.frames_since_change} frames unchanged",
                    change=change.to_dict(),
                )
        return change

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._last_hash.pop(execution_id, None)
        cls._frames_still.pop(execution_id, None)
