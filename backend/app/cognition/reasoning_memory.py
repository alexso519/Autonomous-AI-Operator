"""
Strategic planning memory — hot context for deliberation within an execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.execution.context_manager import ContextManager

COGNITION_KEY = "cognition_state"
PLANNING_KEY = "strategic_planning_memory"


class ReasoningMemory:
    """In-execution reasoning graph and planning artifacts."""

    def __init__(self, ctx: ContextManager) -> None:
        self._ctx = ctx
        self._ensure()

    def _ensure(self) -> None:
        if self._ctx.get_workflow_memory(COGNITION_KEY) is None:
            self._ctx.set_workflow_memory(
                COGNITION_KEY,
                {
                    "hypotheses": [],
                    "branches": [],
                    "debates": [],
                    "uncertaintyHistory": [],
                    "verifications": [],
                    "consensus": None,
                },
            )
        if self._ctx.get_workflow_memory(PLANNING_KEY) is None:
            self._ctx.set_workflow_memory(
                PLANNING_KEY,
                {"plans": [], "activePlanId": None},
            )

    def get_state(self) -> dict[str, Any]:
        return dict(self._ctx.get_workflow_memory(COGNITION_KEY) or {})

    def set_state(self, state: dict[str, Any]) -> None:
        self._ctx.set_workflow_memory(COGNITION_KEY, state)

    def append_hypothesis(self, hypothesis: dict[str, Any]) -> None:
        state = self.get_state()
        hyps = list(state.get("hypotheses") or [])
        hyps.append(hypothesis)
        state["hypotheses"] = hyps[-20:]
        self.set_state(state)

    def append_branch(self, branch: dict[str, Any]) -> None:
        state = self.get_state()
        branches = list(state.get("branches") or [])
        branches.append(branch)
        state["branches"] = branches[-30:]
        self.set_state(state)

    def append_debate(self, debate: dict[str, Any]) -> None:
        state = self.get_state()
        debates = list(state.get("debates") or [])
        debates.append(debate)
        state["debates"] = debates[-15:]
        self.set_state(state)

    def record_uncertainty(self, assessment: dict[str, Any]) -> None:
        state = self.get_state()
        history = list(state.get("uncertaintyHistory") or [])
        history.append(
            {
                **assessment,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["uncertaintyHistory"] = history[-25:]
        state["latestUncertainty"] = assessment
        self.set_state(state)

    def record_verification(self, result: dict[str, Any]) -> None:
        state = self.get_state()
        verifications = list(state.get("verifications") or [])
        verifications.append(result)
        state["verifications"] = verifications[-20:]
        self.set_state(state)

    def set_consensus(self, consensus: dict[str, Any]) -> None:
        state = self.get_state()
        state["consensus"] = consensus
        self.set_state(state)

    def add_plan(self, plan: dict[str, Any]) -> None:
        planning = dict(self._ctx.get_workflow_memory(PLANNING_KEY) or {})
        plans = list(planning.get("plans") or [])
        plans.append(plan)
        planning["plans"] = plans[-10:]
        planning["activePlanId"] = plan.get("id")
        planning["updatedAt"] = datetime.now(timezone.utc).isoformat()
        self._ctx.set_workflow_memory(PLANNING_KEY, planning)

    def snapshot_for_persistence(self) -> dict[str, Any]:
        return {
            "cognition": self.get_state(),
            "planning": self._ctx.get_workflow_memory(PLANNING_KEY) or {},
        }
