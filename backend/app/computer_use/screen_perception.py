"""
Screen perception — full-screen capture and visual analysis pipeline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.computer_use.ocr_engine import OCREngine
from app.computer_use.ui_detector import UIDetector
from app.computer_use.visual_memory import VisualMemory
from app.config.settings import settings

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "desktop_screenshots"

_mss_available = False
try:
    import mss  # type: ignore
    _mss_available = True
except ImportError:
    mss = None  # type: ignore


@dataclass
class PerceptionResult:
    snapshot_id: str
    image_path: str
    width: int
    height: int
    elements: list[dict[str, Any]] = field(default_factory=list)
    ocr_text: str = ""
    fingerprint: str = ""
    change_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "imagePath": self.image_path,
            "width": self.width,
            "height": self.height,
            "elements": self.elements,
            "ocrTextExcerpt": self.ocr_text[:300],
            "fingerprint": self.fingerprint,
            "changeDetected": self.change_detected,
        }


class ScreenPerception:
    """Capture screenshots, run OCR, detect UI elements, maintain visual memory."""

    def __init__(
        self,
        execution_id: str,
        *,
        memory: VisualMemory | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.memory = memory or VisualMemory(
            f"desktop-{execution_id[:8]}",
            max_history=settings.max_screenshot_history,
        )
        self.ocr = OCREngine()
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def capture_screenshot(self) -> tuple[str, int, int]:
        """Capture full screen; returns (path, width, height)."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"{self.execution_id[:8]}_{ts}_{uuid.uuid4().hex[:6]}.png"

        if _mss_available:
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
                from mss.tools import to_png
                to_png(shot.rgb, shot.size, output=str(path))
                return str(path), shot.width, shot.height

        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            width, height = img.size
            img.save(path)
            return str(path), width, height
        except Exception as exc:
            logger.warning("Screenshot capture failed, using placeholder: %s", exc)
            from PIL import Image
            img = Image.new("RGB", (1920, 1080), color=(30, 30, 40))
            width, height = img.size
            img.save(path)
            return str(path), width, height

    async def perceive(
        self,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
        persist: bool = True,
    ) -> PerceptionResult:
        """Full perception cycle: capture → OCR → detect → memory."""
        image_path, width, height = self.capture_screenshot()

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "screenshot_captured",
                agent,
                f"Captured screen {width}x{height}",
                snapshotId=f"snap-{uuid.uuid4().hex[:8]}",
                imagePath=image_path,
                width=width,
                height=height,
            )

        ocr_result = self.ocr.extract(image_path)
        elements = UIDetector.detect_from_ocr(ocr_result)

        if emit_fn:
            for el in elements[:10]:
                await emit_fn(
                    self.execution_id,
                    "ui_element_detected",
                    agent,
                    f"Detected {el.element_type}: {el.label[:40]}",
                    elementId=el.id,
                    elementType=el.element_type,
                    label=el.label,
                    x=el.x,
                    y=el.y,
                    width=el.width,
                    height=el.height,
                    confidence=el.confidence,
                    interactive=el.interactive,
                )

        snap = self.memory.add_snapshot(
            image_path, width, height,
            elements=elements,
            ocr_text=ocr_result.text,
        )
        changed, _ = self.memory.detect_change()

        if persist:
            try:
                from app.computer_use.computer_use_store import (
                    save_ocr_extraction,
                    save_screen_snapshot,
                    save_ui_state,
                    save_visual_element,
                )
                await save_screen_snapshot(
                    self.execution_id,
                    snapshot_id=snap.id,
                    image_path=image_path,
                    width=width,
                    height=height,
                    fingerprint=snap.fingerprint,
                )
                await save_ocr_extraction(
                    self.execution_id, snap.id,
                    engine=ocr_result.engine,
                    text=ocr_result.text,
                    regions=[r.to_dict() for r in ocr_result.regions],
                )
                for el in elements:
                    await save_visual_element(
                        self.execution_id, snap.id,
                        element_id=el.id,
                        element_type=el.element_type,
                        label=el.label,
                        bbox=[el.x, el.y, el.width, el.height],
                        confidence=el.confidence,
                        interactive=el.interactive,
                    )
                await save_ui_state(
                    self.execution_id,
                    snapshot_id=snap.id,
                    state_hash=self.memory.state_hash(),
                    change_detected=changed,
                    state_data=self.memory.to_context_dict(),
                )
            except Exception as exc:
                logger.debug("Persistence skipped: %s", exc)

        return PerceptionResult(
            snapshot_id=snap.id,
            image_path=image_path,
            width=width,
            height=height,
            elements=[e.to_dict() for e in elements],
            ocr_text=ocr_result.text,
            fingerprint=snap.fingerprint,
            change_detected=changed,
        )
