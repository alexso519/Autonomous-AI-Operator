"""Tests for unified action system."""

from app.runtime.action_system import (
    ActionContext,
    ActionPriority,
    ActionResult,
    ActionStatus,
    ActionType,
    RuntimeAction,
)


def test_runtime_action_deterministic_id():
    ctx = ActionContext(execution_id="exec-1", node_id="n1")
    a1 = RuntimeAction(action_type=ActionType.TOOL_CALL, context=ctx)
    a2 = RuntimeAction(action_type=ActionType.TOOL_CALL, context=ctx)
    assert a1.id != a2.id
    assert len(a1.id) == 16


def test_action_cancellation():
    action = RuntimeAction(
        action_type=ActionType.AGENT_EXECUTION,
        context=ActionContext(execution_id="e1"),
    )
    action.request_cancellation()
    assert action.is_cancelled


def test_action_to_dict():
    action = RuntimeAction(
        action_type=ActionType.WEB_RESEARCH,
        context=ActionContext(execution_id="e1", workflow_id="w1"),
        priority=ActionPriority.HIGH,
    )
    d = action.to_dict()
    assert d["actionType"] == "web_research"
    assert d["priority"] == 8


def test_action_result():
    result = ActionResult(
        action_id="abc",
        success=True,
        duration_ms=42.5,
        retry_lineage=["prev"],
    )
    assert result.to_dict()["durationMs"] == 42.5


def test_all_action_types_defined():
    expected = {
        "agent_execution", "tool_call", "web_research", "browser_action",
        "code_patch", "test_run", "memory_retrieval", "synthesis",
        "reflection", "retry", "validation",
    }
    assert {t.value for t in ActionType} == expected
