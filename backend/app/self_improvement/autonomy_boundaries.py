"""
Hard autonomy boundaries — recommendation-first, no code mutation, no escalation.
"""

from __future__ import annotations

from typing import Any

FORBIDDEN_ACTION_TYPES = frozenset({
    "source_rewrite",
    "code_patch",
    "file_write",
    "recursive_evolution",
    "infrastructure_escalation",
    "unsafe_capability_activation",
    "unrestricted_planning",
})

FORBIDDEN_PROPOSAL_FIELDS = frozenset({
    "modifiesSourceCode",
    "writesSourceFiles",
    "deploysInfrastructure",
    "activatesUnsafeCapability",
})


class AutonomyBoundaries:
    """Enforce recommendation-first bounded evolution."""

    @classmethod
    def is_within_boundaries(cls, proposal: dict[str, Any]) -> tuple[bool, str]:
        action_type = str(proposal.get("type", "")).lower()
        if action_type in FORBIDDEN_ACTION_TYPES:
            return False, f"forbidden_action:{action_type}"
        for field in FORBIDDEN_PROPOSAL_FIELDS:
            if proposal.get(field):
                return False, f"forbidden_field:{field}"
        if proposal.get("unboundedRecursion"):
            return False, "unbounded_recursion"
        if proposal.get("autoApply") and not proposal.get("humanApproved"):
            return False, "auto_apply_without_approval"
        return True, "ok"

    @classmethod
    def sanitize_proposal(cls, proposal: dict[str, Any]) -> dict[str, Any]:
        """Ensure proposals are inspectable recommendation-only artifacts."""
        return {
            **proposal,
            "recommendationOnly": True,
            "modifiesSourceCode": False,
            "inspectable": True,
            "replayable": True,
            "requiresApproval": True,
        }

    @classmethod
    def check_coordination_complexity(cls, agent_count: int, max_agents: int = 12) -> tuple[bool, str]:
        if agent_count > max_agents:
            return False, "coordination_complexity_cap"
        return True, "ok"
