"""
Tests for visual grounding layer.
Run with: python -m pytest backend/tests/test_visual_grounding.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.ocr_engine import OCRRegion, OCRResult
from app.computer_use.ui_detector import UIDetector, UIElement
from app.computer_use.visual_grounding import VisualGrounding
from app.computer_use.visual_memory import VisualMemory


def test_ui_detector_classifies_button():
    ocr = OCRResult(
        engine="test",
        text="OK Cancel",
        regions=[
            OCRRegion(text="OK", x=100, y=200, width=40, height=20, confidence=0.9),
            OCRRegion(text="Cancel", x=160, y=200, width=60, height=20, confidence=0.85),
        ],
    )
    elements = UIDetector.detect_from_ocr(ocr)
    assert len(elements) >= 1
    assert any(e.interactive for e in elements)


def test_visual_grounding_finds_target():
    memory = VisualMemory("test-session")
    memory.add_snapshot(
        "fake.png", 1920, 1080,
        elements=[
            UIElement(
                id="ui-1", element_type="button", label="Submit",
                x=500, y=300, width=80, height=30, confidence=0.9, interactive=True,
            ),
        ],
    )
    grounding = VisualGrounding(memory)
    target = grounding.ground("Submit")
    assert target is not None
    assert target.label == "Submit"
    assert target.center == (540, 315)
    assert grounding.score_click_confidence(target) >= 0.8


def test_visual_grounding_no_match():
    memory = VisualMemory("test-session")
    memory.add_snapshot("fake.png", 800, 600, elements=[])
    grounding = VisualGrounding(memory)
    assert grounding.ground("Nonexistent") is None
