"""
Budget enforcement and execution throttling by spend.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class BudgetEnforcer:
    """Enforces per-tenant execution budgets."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_cost_metering

    @staticmethod
    async def ensure_budget(tenant_id: str = "default") -> dict[str, Any]:
        if not BudgetEnforcer.enabled():
            return {"limit": settings.default_execution_budget, "spent": 0}
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT * FROM usage_budgets WHERE tenant_id = ? AND status = 'active' LIMIT 1",
                (tenant_id,),
            )
            row = await cursor.fetchone()
            now = datetime.now(timezone.utc).isoformat()
            if row:
                return {
                    "budgetId": row["id"],
                    "limit": row["budget_limit"],
                    "spent": row["spent"],
                    "period": row["period"],
                }
            bid = f"bud_{uuid.uuid4().hex[:12]}"
            limit = settings.default_execution_budget
            await db.execute(
                """INSERT INTO usage_budgets
                   (id, tenant_id, budget_limit, spent, period, status, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 'monthly', 'active', ?, ?)""",
                (bid, tenant_id, limit, now, now),
            )
            await db.commit()
            return {"budgetId": bid, "limit": limit, "spent": 0, "period": "monthly"}
        except Exception as exc:
            logger.debug("Budget ensure skipped: %s", exc)
            return {"limit": settings.default_execution_budget, "spent": 0}

    @staticmethod
    async def check_and_charge(
        execution_id: str,
        amount: float,
        *,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        if not BudgetEnforcer.enabled():
            return {"allowed": True}
        try:
            budget = await BudgetEnforcer.ensure_budget(tenant_id)
            limit = float(budget.get("limit", settings.default_execution_budget))
            spent = float(budget.get("spent", 0))
            projected = spent + amount

            if projected >= limit * 0.9 and projected < limit:
                await emit_global_infrastructure_event(
                    "budget_warning",
                    f"Budget at {round(projected/limit*100)}% for {tenant_id}",
                    tenantId=tenant_id,
                    spent=projected,
                    limit=limit,
                )

            if projected > limit:
                await emit_global_infrastructure_event(
                    "budget_exceeded",
                    f"Budget exceeded for {tenant_id}",
                    tenantId=tenant_id,
                    spent=projected,
                    limit=limit,
                    executionId=execution_id,
                )
                return {"allowed": False, "reason": "budget_exceeded", "spent": projected, "limit": limit}

            if budget.get("budgetId"):
                now = datetime.now(timezone.utc).isoformat()
                db = await get_db()
                await db.execute(
                    "UPDATE usage_budgets SET spent = ?, updated_at = ? WHERE id = ?",
                    (projected, now, budget["budgetId"]),
                )
                await db.commit()

            await emit_global_infrastructure_event(
                "runtime_cost_updated",
                "Budget updated",
                tenantId=tenant_id,
                spent=projected,
            )
            return {"allowed": True, "spent": projected, "limit": limit}
        except Exception as exc:
            logger.warning("Budget check degraded (allow): %s", exc)
            return {"allowed": True, "degraded": True}
