"""
Tests for semantic UI intelligence.
Run with: python -m pytest backend/tests/test_semantic_ui.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.ui_semantic_parser import UISemanticParser
from app.computer_use.semantic_ui_memory import SemanticUIMemory
from app.computer_use.layout_reasoner import LayoutReasoner
from app.computer_use.interaction_predictor import InteractionPredictor


def test_semantic_parser_classifies_dialog():
    parser = UISemanticParser()
    elements = [
        {"id": "1", "label": "OK", "elementType": "button", "bbox": [100, 200, 150, 230]},
        {"id": "2", "label": "Cancel", "elementType": "button", "bbox": [160, 200, 220, 230]},
    ]
    result = parser.parse(elements, ocr_text="Confirm action OK Cancel")
    assert result.dominant_type == "dialog"
    assert result.workflow_hint == "confirmation_pending"


def test_semantic_memory_similarity():
    mem = SemanticUIMemory("test-sem")
    mem.record("dialog", ["OK", "Cancel"], 2)
    similar = mem.find_similar("dialog", ["OK", "Cancel"], 2)
    assert similar
    assert similar[0][1] >= 0.5
    SemanticUIMemory.cleanup("test-sem")


def test_layout_reasoner():
    reasoner = LayoutReasoner()
    elements = [
        {"elementId": "a", "bbox": [10, 10, 100, 40], "label": "File"},
        {"elementId": "b", "bbox": [200, 300, 400, 350], "label": "Content"},
    ]
    analysis = reasoner.reason(elements)
    assert analysis.layout_type in ("standard_app", "complex", "toolbar", "empty")
    assert analysis.regions


async def _run_predict_and_emit():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    parser = UISemanticParser()
    semantic = parser.parse([{"id": "1", "label": "Submit", "elementType": "button"}])
    reasoner = LayoutReasoner()
    layout = reasoner.reason([e.to_dict() for e in semantic.elements])

    predictor = InteractionPredictor()
    await predictor.predict_and_emit(
        "test-pred",
        semantic.to_dict(),
        layout.to_dict(),
        emit_fn=emit_fn,
    )
    assert "interaction_predicted" in emitted


def test_interaction_predictor():
    asyncio.run(_run_predict_and_emit())
