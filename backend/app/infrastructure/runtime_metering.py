"""
Runtime economics facade — metering, budgets, and efficiency analytics.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RuntimeMetering:
    """Central metering facade for execution lifecycle."""

    _instance: RuntimeMetering | None = None

    @classmethod
    def get_instance(cls) -> RuntimeMetering:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return settings.enable_cost_metering

    async def on_model_invocation(
        self,
        execution_id: str,
        *,
        tokens: int = 0,
        model: str = "",
        tenant_id: str = "default",
    ) -> None:
        if not self.enabled():
            return
        try:
            from app.infrastructure.cost_tracker import CostTracker
            from app.infrastructure.usage_allocator import UsageAllocator

            await CostTracker.record_cost(
                execution_id,
                "model_token",
                tokens,
                tenant_id=tenant_id,
                metadata={"model": model},
            )
            usage = await self._get_usage(execution_id)
            await UsageAllocator.upsert_execution_usage(
                execution_id,
                tenant_id=tenant_id,
                model_tokens=usage.get("modelTokens", 0) + tokens,
                tool_calls=usage.get("toolCalls", 0),
            )
        except Exception as exc:
            logger.debug("Model metering skipped: %s", exc)

    async def on_tool_execution(
        self,
        execution_id: str,
        *,
        tenant_id: str = "default",
    ) -> None:
        if not self.enabled():
            return
        try:
            from app.infrastructure.cost_tracker import CostTracker

            await CostTracker.record_cost(execution_id, "tool_call", 1, tenant_id=tenant_id)
        except Exception as exc:
            logger.debug("Tool metering skipped: %s", exc)

    async def on_execution_complete(
        self,
        execution_id: str,
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        if not self.enabled():
            return {}
        try:
            from app.infrastructure.cost_tracker import CostTracker
            from app.infrastructure.budget_enforcer import BudgetEnforcer

            total = await CostTracker.get_execution_total(execution_id)
            charge = await BudgetEnforcer.check_and_charge(
                execution_id, total, tenant_id=tenant_id
            )
            return {"totalCost": total, "budget": charge}
        except Exception as exc:
            logger.debug("Metering complete skipped: %s", exc)
            return {}

    async def get_dashboard(self, *, tenant_id: str = "default") -> dict[str, Any]:
        try:
            from app.infrastructure.budget_enforcer import BudgetEnforcer
            from app.infrastructure.usage_allocator import UsageAllocator

            budget = await BudgetEnforcer.ensure_budget(tenant_id)
            forecast = await UsageAllocator.forecast_quota(tenant_id)
            return {"budget": budget, "forecast": forecast}
        except Exception as exc:
            logger.debug("Metering dashboard skipped: %s", exc)
            return {}

    async def _get_usage(self, execution_id: str) -> dict[str, Any]:
        try:
            from app.database.database import get_db

            db = await get_db()
            cursor = await db.execute(
                "SELECT * FROM execution_usage WHERE execution_id = ?", (execution_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return {}
            return {
                "modelTokens": row["model_tokens"],
                "toolCalls": row["tool_calls"],
                "browserActions": row["browser_actions"],
                "totalCost": row["total_cost"],
            }
        except Exception:
            return {}
