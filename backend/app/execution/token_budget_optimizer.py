"""
Dynamic context and token budget optimizer.

Role-based allocation, adaptive compression, and synthesis-focused context.

Compatibility wrapper — delegates to ContextBudgetManager.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_budget_manager import (
    ContextBudgetManager,
    RoleBudgetProfile,
    estimate_tokens,
)
from app.execution.context_compression import ContextBudget

# Re-export for backward compatibility
RoleBudget = RoleBudgetProfile
from app.execution.context_budget_manager import ROLE_PROFILES as ROLE_BUDGETS


def resolve_role_budget(
    model_tier: str = "standard",
    is_synthesis: bool = False,
    is_retry: bool = False,
) -> RoleBudgetProfile:
    return ContextBudgetManager.resolve_profile(model_tier, is_synthesis, is_retry)


def role_budget_to_context_budget(role: RoleBudgetProfile) -> ContextBudget:
    return ContextBudgetManager.to_context_budget(role)


def build_role_context(
    initial_context: str,
    outputs: dict[str, dict[str, Any]],
    tool_calls: list[dict[str, Any]] | None,
    model_tier: str = "standard",
    is_synthesis: bool = False,
    is_retry: bool = False,
) -> tuple[str, dict[str, Any]]:
    """
    Build compressed context with role-based budget.

    Returns (context_string, budget_metadata).
    """
    profile = resolve_role_budget(model_tier, is_synthesis, is_retry)
    context, meta = ContextBudgetManager.build_workflow_context(
        initial_context=initial_context,
        outputs=outputs,
        tool_calls=tool_calls,
        profile=profile,
        is_synthesis=is_synthesis,
    )
    meta["modelTier"] = model_tier
    meta["isRetry"] = is_retry
    return context, meta


def estimate_context_tokens(
    initial_context: str,
    outputs: dict[str, dict[str, Any]],
    tool_calls: list[dict[str, Any]] | None,
) -> int:
    block, _ = build_role_context(
        initial_context, outputs, tool_calls, model_tier="standard"
    )
    return estimate_tokens(block)
