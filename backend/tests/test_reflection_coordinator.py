"""Tests for reflection coordinator hedge suppression."""

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
_cm_spec = importlib.util.spec_from_file_location("context_manager", _cm_path)
_cm_mod = importlib.util.module_from_spec(_cm_spec)
assert _cm_spec.loader is not None
_cm_spec.loader.exec_module(_cm_mod)
ContextManager = _cm_mod.ContextManager

_rc_path = Path(__file__).parent.parent / "app" / "execution" / "reflection_coordinator.py"
_rc_spec = importlib.util.spec_from_file_location("reflection_coordinator", _rc_path)
_rc_mod = importlib.util.module_from_spec(_rc_spec)
assert _rc_spec.loader is not None
_rc_spec.loader.exec_module(_rc_mod)
ReflectionCoordinator = _rc_mod.ReflectionCoordinator


HEDGE_OUTPUT = (
    "# Summary\n"
    "This might possibly be a reasonable approach that could work in some cases. "
    "It seems like the strategy suggests we may be able to proceed, though I think "
    "there is likely some uncertainty. Perhaps we should consider the options carefully "
    "and probably evaluate next steps.\n\n"
    "## Findings\n"
    "- The plan could include several phases that might help achieve objectives\n"
    "- Milestones may be defined quarterly with flexible checkpoints\n"
    "- Resources might be allocated based on likely priority areas\n\n"
    "## Recommendations\n"
    "- We should probably start with a pilot phase that could validate assumptions\n"
    "- Teams might focus on high-impact workstreams first\n"
    "- Progress seems achievable if we likely maintain consistent cadence\n\n"
    "## Next Steps\n"
    "- Schedule a review that may clarify open questions\n"
    "- Document assumptions we think are reasonable\n"
    "- Iterate on the roadmap as new information possibly emerges"
)


def test_hedge_wording_alone_does_not_trigger_retry() -> None:
    ctx = ContextManager()
    ctx.add_output("agent-0", "Planner Agent", HEDGE_OUTPUT)
    node = {
        "id": "agent-0",
        "data": {
            "label": "Planner Agent",
            "goal": "Create a detailed strategy and roadmap for execution",
        },
    }

    coordinator = ReflectionCoordinator("exec-hedge-001", ctx)
    outcome = coordinator.evaluate(node)

    assert outcome.should_replan is False
    assert outcome.suppressed is True
    assert outcome.suppression_reason == "hedge_only"


def test_recovery_node_suppressed() -> None:
    ctx = ContextManager()
    ctx.add_output("retry-1", "Retry Agent", "Bad output")
    node = {
        "id": "retry-1",
        "data": {
            "label": "Retry Agent",
            "goal": "Fix the output",
            "executionMetadata": {"recovery": True},
        },
    }

    coordinator = ReflectionCoordinator("exec-recovery-001", ctx)
    outcome = coordinator.evaluate(node)

    assert outcome.should_replan is False
    assert outcome.action == "suppressed_recovery_node"


if __name__ == "__main__":
    test_hedge_wording_alone_does_not_trigger_retry()
    test_recovery_node_suppressed()
    print("All reflection coordinator tests passed.")
