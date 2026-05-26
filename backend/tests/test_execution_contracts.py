"""Tests for execution contracts."""

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
sys.modules["app.execution.context_manager"] = _cm_mod

_eq_path = Path(__file__).parent.parent / "app" / "execution" / "execution_quality.py"
_eq_spec = importlib.util.spec_from_file_location("execution_quality_test", _eq_path)
_eq_mod = importlib.util.module_from_spec(_eq_spec)
sys.modules[_eq_spec.name] = _eq_mod
assert _eq_spec.loader is not None
_eq_spec.loader.exec_module(_eq_mod)
ExecutionQualityScore = _eq_mod.ExecutionQualityScore
QualityDimension = _eq_mod.QualityDimension
sys.modules["app.execution.execution_quality"] = _eq_mod

_rp_mod = importlib.import_module("app.execution.retry_policy")
sys.modules["app.execution.retry_policy"] = _rp_mod

_rs_path = Path(__file__).parent.parent / "app" / "execution" / "runtime_state.py"
_rs_spec = importlib.util.spec_from_file_location("runtime_state_test", _rs_path)
_rs_mod = importlib.util.module_from_spec(_rs_spec)
sys.modules[_rs_spec.name] = _rs_mod
assert _rs_spec.loader is not None
_rs_spec.loader.exec_module(_rs_mod)
RuntimeState = _rs_mod.RuntimeState
sys.modules["app.execution.runtime_state"] = _rs_mod

_ct_path = Path(__file__).parent.parent / "app" / "execution" / "contracts.py"
_ct_spec = importlib.util.spec_from_file_location("contracts_test", _ct_path)
_ct_mod = importlib.util.module_from_spec(_ct_spec)
sys.modules[_ct_spec.name] = _ct_mod
assert _ct_spec.loader is not None
_ct_spec.loader.exec_module(_ct_mod)

ExecutionContextContract = _ct_mod.ExecutionContextContract
QualityResultContract = _ct_mod.QualityResultContract
ReflectionOutcome = _ct_mod.ReflectionOutcome
RetryIntent = _ct_mod.RetryIntent
RetryRequest = _ct_mod.RetryRequest
RuntimeTransition = _ct_mod.RuntimeTransition


def test_execution_context_contract() -> None:
    ctx = ExecutionContextContract(
        execution_id="exec-001",
        workflow_id="wf-001",
        workflow_name="Test",
        workflow_description="Do something",
        node_count=3,
        global_retry_count=1,
    )
    assert ctx.execution_id == "exec-001"
    assert ctx.node_count == 3


def test_retry_request_typed_intent() -> None:
    request = RetryRequest(
        execution_id="exec-001",
        node_id="node-1",
        agent_name="Agent",
        intent=RetryIntent.TOOL,
        reason="tool_execution_failure",
    )
    assert request.intent == RetryIntent.TOOL


def test_quality_result_from_score() -> None:
    score = ExecutionQualityScore(
        node_id="n1",
        agent_name="Agent",
        overall_score=0.75,
        factuality_confidence=0.8,
        completion_confidence=0.7,
        repetition_score=0.1,
        hallucination_risk=0.05,
        tool_usage_quality=0.9,
        output_usefulness=0.8,
        dimensions={"factuality": QualityDimension("factuality", 0.8, 0.25)},
        reasons=["ok"],
        issues=[],
        should_retry=False,
        retry_hints=[],
    )
    result = QualityResultContract.from_score(score)
    assert result.overall_score == 0.75
    assert result.should_retry is False
    assert result.score is score


def test_runtime_transition_record() -> None:
    transition = RuntimeTransition(
        execution_id="exec-001",
        from_state=RuntimeState.RUNNING,
        to_state=RuntimeState.COMPLETED,
        reason="Done",
        phase="completed",
        transition_id="t-001",
    )
    assert transition.from_state == RuntimeState.RUNNING
    assert transition.to_state == RuntimeState.COMPLETED


def test_reflection_outcome_suppression_fields() -> None:
    outcome = ReflectionOutcome(
        node_id="n1",
        agent_name="Agent",
        should_replan=False,
        action="hedge_suppressed",
        recovery_type="none",
        retry_type="none",
        reason="Hedge only",
        reasoning_trail=["hedge_only_suppressed"],
        quality_score=0.65,
        issues=[],
        retry_count=0,
        suppressed=True,
        suppression_reason="hedge_only",
    )
    assert outcome.suppressed is True
    assert outcome.suppression_reason == "hedge_only"


if __name__ == "__main__":
    test_execution_context_contract()
    test_retry_request_typed_intent()
    test_quality_result_from_score()
    test_runtime_transition_record()
    test_reflection_outcome_suppression_fields()
    print("All execution contracts tests passed.")
