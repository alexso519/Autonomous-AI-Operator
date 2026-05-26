"""Tests for execution quality scoring and retry policy."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Minimal stubs
crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

cm_path = ROOT / "app" / "execution" / "context_manager.py"
cm_spec = importlib.util.spec_from_file_location("context_manager", cm_path)
cm_mod = importlib.util.module_from_spec(cm_spec)
sys.modules["context_manager"] = cm_mod
cm_spec.loader.exec_module(cm_mod)
ContextManager = cm_mod.ContextManager

app_pkg = ModuleType("app")
exec_pkg = ModuleType("app.execution")
exec_pkg.context_manager = cm_mod
app_pkg.execution = exec_pkg
sys.modules["app"] = app_pkg
sys.modules["app.execution"] = exec_pkg
sys.modules["app.execution.context_manager"] = cm_mod

eq_path = ROOT / "app" / "execution" / "execution_quality.py"
eq_spec = importlib.util.spec_from_file_location("app.execution.execution_quality", eq_path)
eq_mod = importlib.util.module_from_spec(eq_spec)
sys.modules["app.execution.execution_quality"] = eq_mod
eq_spec.loader.exec_module(eq_mod)
ExecutionQualityScorer = eq_mod.ExecutionQualityScorer

rp_path = ROOT / "app" / "execution" / "retry_policy.py"
rp_spec = importlib.util.spec_from_file_location("app.execution.retry_policy", rp_path)
rp_mod = importlib.util.module_from_spec(rp_spec)
sys.modules["app.execution.retry_policy"] = rp_mod
rp_spec.loader.exec_module(rp_mod)
RetryPolicy = rp_mod.RetryPolicy
RetryType = rp_mod.RetryType


def test_quality_scores_low_output():
    ctx = ContextManager()
    ctx.add_output("agent-0", "Planner", "Too short.")
    node = {
        "id": "agent-0",
        "data": {
            "label": "Planner",
            "goal": "Create a detailed strategy and roadmap for execution",
        },
    }
    score = ExecutionQualityScorer.score_output(node, ctx)
    assert score.should_retry
    assert score.completion_confidence < 0.5
    assert "incomplete_output" in score.issues


def test_quality_detects_repetition():
    ctx = ContextManager()
    ctx.add_output("agent-0", "A", "This is a long repeated paragraph about AI systems and workflows.")
    repeated = "This is a long repeated paragraph about AI systems and workflows. " * 5
    ctx.add_output("agent-1", "B", repeated)
    node = {"id": "agent-1", "data": {"label": "B", "goal": "Summarize findings"}}
    score = ExecutionQualityScorer.score_output(node, ctx)
    assert score.repetition_score > 0 or score.overall_score < 0.7


def test_retry_policy_classifies_tool_failure():
    ctx = ContextManager()
    ctx.add_output("agent-0", "Researcher", "I think the answer might be X without sources.")
    node = {
        "id": "agent-0",
        "data": {"label": "Researcher", "goal": "Research latest AI news online"},
    }
    quality = ExecutionQualityScorer.score_output(node, ctx)
    retry_type = RetryPolicy.classify_failure(quality)
    assert retry_type in (RetryType.TOOL_RETRY, RetryType.FACTUAL_RETRY, RetryType.EXPAND)


def test_retry_budget_enforcement():
    ctx = ContextManager()
    for _ in range(5):
        RetryPolicy.increment_global_retry(ctx)
    can, reason = RetryPolicy.can_retry(ctx, "agent-0")
    assert not can
    assert "budget" in reason


if __name__ == "__main__":
    test_quality_scores_low_output()
    test_quality_detects_repetition()
    test_retry_policy_classifies_tool_failure()
    test_retry_budget_enforcement()
    print("Execution quality tests passed.")
