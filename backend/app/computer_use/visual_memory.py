"""
Visual memory — screen-state snapshots, embeddings, and change detection.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.computer_use.ui_detector import UIElement


@dataclass
class ScreenSnapshot:
    id: str
    image_path: str
    width: int
    height: int
    fingerprint: str
    elements: list[UIElement] = field(default_factory=list)
    ocr_text: str = ""
    captured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "imagePath": self.image_path,
            "width": self.width,
            "height": self.height,
            "fingerprint": self.fingerprint,
            "elements": [e.to_dict() for e in self.elements],
            "ocrTextExcerpt": self.ocr_text[:500],
            "capturedAt": self.captured_at,
            "metadata": self.metadata,
        }


class VisualMemory:
    """Screen-state memory with temporal change detection."""

    def __init__(self, session_id: str, *, max_history: int = 50) -> None:
        self.session_id = session_id
        self.max_history = max_history
        self.snapshots: list[ScreenSnapshot] = []
        self._element_index: dict[str, UIElement] = {}

    @staticmethod
    def compute_fingerprint(image_path: str) -> str:
        try:
            from PIL import Image

            with Image.open(image_path) as img:
                thumb = img.resize((16, 16)).convert("L")
                pixels = list(thumb.getdata())
            return hashlib.sha256(bytes(pixels)).hexdigest()[:16]
        except Exception:
            try:
                with open(image_path, "rb") as f:
                    return hashlib.sha256(f.read(4096)).hexdigest()[:16]
            except Exception:
                return hashlib.sha256(image_path.encode()).hexdigest()[:16]

    def add_snapshot(
        self,
        image_path: str,
        width: int,
        height: int,
        *,
        elements: list[UIElement] | None = None,
        ocr_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ScreenSnapshot:
        snap = ScreenSnapshot(
            id=f"vsnap-{uuid.uuid4().hex[:10]}",
            image_path=image_path,
            width=width,
            height=height,
            fingerprint=self.compute_fingerprint(image_path),
            elements=elements or [],
            ocr_text=ocr_text,
            metadata=metadata or {},
        )
        self.snapshots.append(snap)
        for el in snap.elements:
            self._element_index[el.id] = el
        if len(self.snapshots) > self.max_history:
            self.snapshots = self.snapshots[-self.max_history:]
        return snap

    @property
    def latest(self) -> ScreenSnapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def detect_change(self) -> tuple[bool, float]:
        """Return (changed, similarity) comparing last two snapshots."""
        if len(self.snapshots) < 2:
            return False, 1.0
        prev, curr = self.snapshots[-2], self.snapshots[-1]
        if prev.fingerprint == curr.fingerprint:
            return False, 1.0
        prev_labels = {e.label for e in prev.elements}
        curr_labels = {e.label for e in curr.elements}
        union = prev_labels | curr_labels
        if not union:
            return True, 0.0
        similarity = len(prev_labels & curr_labels) / len(union)
        return similarity < 0.85, similarity

    def get_element(self, element_id: str) -> UIElement | None:
        return self._element_index.get(element_id)

    def state_hash(self) -> str:
        latest = self.latest
        if not latest:
            return ""
        labels = sorted(e.label for e in latest.elements)
        payload = f"{latest.fingerprint}|{'|'.join(labels)}"
        return hashlib.md5(payload.encode()).hexdigest()[:12]

    def to_context_dict(self) -> dict[str, Any]:
        latest = self.latest
        return {
            "sessionId": self.session_id,
            "snapshotCount": len(self.snapshots),
            "latestSnapshot": latest.to_dict() if latest else None,
            "stateHash": self.state_hash(),
            "interactiveElements": [
                e.to_dict() for e in (latest.elements if latest else []) if e.interactive
            ][:20],
        }
