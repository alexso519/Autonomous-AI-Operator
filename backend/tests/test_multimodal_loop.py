"""
Tests for multimodal perception-action loop.
Run with: python -m pytest backend/tests/test_multimodal_loop.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.action_planner import ActionPlanner
from app.computer_use.interaction_reflection import InteractionReflection
from app.computer_use.multimodal_loop import MultimodalLoop


def test_action_planner_generates_steps():
    plan = ActionPlanner.plan("Click the Submit button on screen")
    assert len(plan.steps) >= 2
    actions = [s.action for s in plan.steps]
    assert "observe" in actions
    assert "click" in actions


def test_interaction_reflection_retry():
    reflection = InteractionReflection()
    result = reflection.reflect("click", "Submit", "grounding_failed", attempt=1)
    assert result.should_retry
    assert result.recovery_strategy == "recapture_and_reground"


def test_interaction_reflection_escalates():
    reflection = InteractionReflection()
    for i in range(1, 5):
        result = reflection.reflect("click", "Submit", "failed", attempt=i)
    assert not result.should_retry
    assert result.recovery_strategy == "escalate_to_human"


async def _run_multimodal_loop_dry():
    loop = MultimodalLoop("test-exec-multimodal")
    emitted: list[str] = []

    async def emit_fn(exec_id, event_type, agent, message, **data):
        emitted.append(event_type)

    result = await loop.run(
        "Analyze the current screen layout",
        emit_fn=emit_fn,
        agent="WorkflowExecutor",
    )
    assert "results" in result
    assert "multimodal_reasoning_started" in emitted
    await loop.cleanup()


def test_multimodal_loop_runs_dry():
    asyncio.run(_run_multimodal_loop_dry())


def test_multimodal_loop_sync():
    asyncio.run(_run_multimodal_loop_dry())
