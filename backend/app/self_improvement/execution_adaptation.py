"""
Execution reliability adaptation — budget and recovery strategy hints.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.config.settings import settings

EmitFn = Callable[..., Awaitable[None]]


class ExecutionAdaptation:
    @classmethod
    async def adapt_reliability(
        cls,
        execution_id: str,
        status: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        budget_factor = 1.0
        if status == "failed":
            budget_factor = 0.85
        retries = int(ctx_snapshot.get("global_retry_count") or 0)
        if retries > 4:
            budget_factor *= 0.9

        profile = {
            "executionBudgetScale": round(budget_factor, 2),
            "recoveryStrategy": "debate_plus_tools" if status == "failed" else "standard",
            "reliabilityScore": 0.8 if status == "completed" else 0.45,
        }

        if emit_fn:
            await emit_fn(
                execution_id,
                "execution_strategy_reweighted",
                "self_improvement",
                "Execution reliability profile updated",
                profile=profile,
                defaultBudget=settings.default_execution_budget,
            )
        return profile
