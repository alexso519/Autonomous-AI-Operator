"""Tests for unified retry coordinator."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

sys.path.insert(0, str(Path(__file__).parent.parent))

_cm_path = Path(__file__).parent.parent / "app" / "execution" / "context_manager.py"
_cm_spec = importlib.util.spec_from_file_location("context_manager_test", _cm_path)
_cm_mod = importlib.util.module_from_spec(_cm_spec)
sys.modules[_cm_spec.name] = _cm_mod
assert _cm_spec.loader is not None
_cm_spec.loader.exec_module(_cm_mod)
ContextManager = _cm_mod.ContextManager
sys.modules["app.execution.context_manager"] = _cm_mod

_eq_path = Path(__file__).parent.parent / "app" / "execution" / "execution_quality.py"
_eq_spec = importlib.util.spec_from_file_location("execution_quality_test", _eq_path)
_eq_mod = importlib.util.module_from_spec(_eq_spec)
sys.modules[_eq_spec.name] = _eq_mod
assert _eq_spec.loader is not None
_eq_spec.loader.exec_module(_eq_mod)
ExecutionQualityScorer = _eq_mod.ExecutionQualityScorer
sys.modules["app.execution.execution_quality"] = _eq_mod

_rc_path = Path(__file__).parent.parent / "app" / "execution" / "retry_coordinator.py"
_rc_spec = importlib.util.spec_from_file_location("retry_coordinator_test", _rc_path)
_rc_mod = importlib.util.module_from_spec(_rc_spec)
sys.modules[_rc_spec.name] = _rc_mod
assert _rc_spec.loader is not None
_rc_spec.loader.exec_module(_rc_mod)
RetryCoordinator = _rc_mod.RetryCoordinator

_ct_path = Path(__file__).parent.parent / "app" / "execution" / "contracts.py"
sys.modules["app.execution.retry_policy"] = importlib.import_module("app.execution.retry_policy")
_ct_spec = importlib.util.spec_from_file_location("contracts_test", _ct_path)
_ct_mod = importlib.util.module_from_spec(_ct_spec)
sys.modules[_ct_spec.name] = _ct_mod
assert _ct_spec.loader is not None
_ct_spec.loader.exec_module(_ct_mod)
RetryIntent = _ct_mod.RetryIntent
RetryRequest = _ct_mod.RetryRequest


def _make_node(node_id: str = "agent-0") -> dict:
    return {
        "id": node_id,
        "data": {
            "label": "Test Agent",
            "goal": "Create a detailed research report with findings and recommendations",
        },
    }


def _low_quality_score(node: dict, ctx: ContextManager):
    ctx.add_output(node["id"], "Test Agent", "Too short.")
    return ExecutionQualityScorer.score_output(node, ctx)


def test_retry_coordinator_allows_typed_retry() -> None:
    ctx = ContextManager()
    node = _make_node()
    quality = _low_quality_score(node, ctx)

    coordinator = RetryCoordinator("exec-retry-001", ctx)
    request = RetryRequest(
        execution_id="exec-retry-001",
        node_id=node["id"],
        agent_name="Test Agent",
        intent=RetryIntent.REFLECTION,
        reason="low_quality",
        quality=quality,
        prior_output="Too short.",
    )
    result = coordinator.evaluate(request)

    assert result.allowed is True
    assert result.decision is not None
    assert result.retry_type is not None


def test_retry_coordinator_enforces_budget() -> None:
    ctx = ContextManager()
    ctx.set_workflow_memory("global_retry_count", 5)
    node = _make_node()
    quality = _low_quality_score(node, ctx)

    coordinator = RetryCoordinator("exec-retry-budget", ctx)
    request = RetryRequest(
        execution_id="exec-retry-budget",
        node_id=node["id"],
        agent_name="Test Agent",
        intent=RetryIntent.REFLECTION,
        reason="low_quality",
        quality=quality,
        prior_output="Too short.",
    )
    result = coordinator.evaluate(request)

    assert result.allowed is False
    assert "budget" in result.block_reason


def test_retry_coordinator_deduplicates() -> None:
    ctx = ContextManager()
    node = _make_node()
    quality = _low_quality_score(node, ctx)

    coordinator = RetryCoordinator("exec-retry-dedupe", ctx)
    request = RetryRequest(
        execution_id="exec-retry-dedupe",
        node_id=node["id"],
        agent_name="Test Agent",
        intent=RetryIntent.REFLECTION,
        reason="low_quality",
        quality=quality,
        prior_output="Too short.",
        output_hash="abc123",
    )

    first = coordinator.evaluate(request)
    assert first.allowed is True

    second = coordinator.evaluate(request)
    assert second.allowed is False
    assert second.deduplicated is True


def test_retry_coordinator_replay_log() -> None:
    ctx = ContextManager()
    node = _make_node()
    quality = _low_quality_score(node, ctx)

    coordinator = RetryCoordinator("exec-retry-replay", ctx)
    request = RetryRequest(
        execution_id="exec-retry-replay",
        node_id=node["id"],
        agent_name="Test Agent",
        intent=RetryIntent.TOOL,
        reason="tool_failure",
        quality=quality,
        prior_output="Too short.",
    )
    coordinator.evaluate(request)

    log = coordinator.get_replay_log()
    assert len(log) >= 1
    assert log[0]["intent"] == "tool"

    RetryCoordinator.cleanup("exec-retry-replay")


if __name__ == "__main__":
    test_retry_coordinator_allows_typed_retry()
    test_retry_coordinator_enforces_budget()
    test_retry_coordinator_deduplicates()
    test_retry_coordinator_replay_log()
    print("All retry coordinator tests passed.")
