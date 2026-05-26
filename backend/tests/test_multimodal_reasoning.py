"""
Tests for multimodal reasoning.
Run with: python -m pytest backend/tests/test_multimodal_reasoning.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.multimodal_reasoner import MultimodalReasoner
from app.computer_use.contextual_perception import ContextualPerception
from app.computer_use.screen_change_detector import ScreenChangeDetector
from app.computer_use.attention_manager import AttentionManager


def test_screen_change_detector():
    detector = ScreenChangeDetector("test-change")
    first = detector.detect(ocr_text="Hello world", element_count=3)
    assert first.current_hash
    second = detector.detect(ocr_text="Hello world", element_count=3)
    assert not second.change_detected
    third = detector.detect(ocr_text="Different content", element_count=5)
    assert third.change_detected
    ScreenChangeDetector.cleanup("test-change")


async def _run_change_emit():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    detector = ScreenChangeDetector("test-change-emit")
    detector.detect(ocr_text="baseline", element_count=1)
    await detector.detect_and_emit(ocr_text="changed text", element_count=2, emit_fn=emit_fn)
    assert "screen_change_detected" in emitted
    ScreenChangeDetector.cleanup("test-change-emit")


def test_screen_change_emit():
    asyncio.run(_run_change_emit())


def test_multimodal_reasoner():
    reasoner = MultimodalReasoner("test-mm")
    ctx = reasoner.reason(
        ocr_text="File Edit View",
        semantic_ui={"dominantType": "menu", "elementCount": 4},
        layout={"layoutType": "standard_app"},
    )
    assert ctx.confidence > 0.5
    assert ctx.semantic_dominant == "menu"


def test_contextual_perception():
    cp = ContextualPerception("test-ctx")
    ctx = cp.perceive(
        active_app="Chrome",
        open_apps=["Chrome", "Terminal"],
        screen_text="Download complete",
    )
    assert "browser_download_pending" in ctx.cross_app_hints


async def _run_attention():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    mgr = AttentionManager("test-att")
    elements = [
        {"bbox": [10, 10, 100, 40], "label": "OK", "interactive": True, "semanticType": "button"},
    ]
    await mgr.shift_attention(elements, emit_fn=emit_fn)
    assert "attention_shifted" in emitted
    AttentionManager.cleanup("test-att")


def test_attention_manager():
    asyncio.run(_run_attention())
