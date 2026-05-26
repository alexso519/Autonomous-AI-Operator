"""
Per-tenant usage allocation and quota forecasting.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class UsageAllocator:
    """Allocates execution usage across tenants."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_cost_metering or settings.enable_multi_tenancy

    @staticmethod
    async def upsert_execution_usage(
        execution_id: str,
        *,
        tenant_id: str = "default",
        model_tokens: int = 0,
        tool_calls: int = 0,
        browser_actions: int = 0,
        desktop_actions: int = 0,
        worker_cost: float = 0,
        storage_bytes: int = 0,
    ) -> dict[str, Any]:
        if not UsageAllocator.enabled():
            return {"allocated": False}
        try:
            from app.infrastructure.cost_tracker import CostTracker

            total = CostTracker.estimate(
                model_tokens=model_tokens,
                tool_calls=tool_calls,
                browser_actions=browser_actions,
                desktop_actions=desktop_actions,
                worker_seconds=worker_cost,
                storage_kb=storage_bytes / 1024,
            )
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            cursor = await db.execute(
                "SELECT id FROM execution_usage WHERE execution_id = ?", (execution_id,)
            )
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    """UPDATE execution_usage SET model_tokens = ?, tool_calls = ?,
                       browser_actions = ?, desktop_actions = ?, worker_cost = ?,
                       storage_bytes = ?, total_cost = ?, updated_at = ?
                       WHERE execution_id = ?""",
                    (
                        model_tokens,
                        tool_calls,
                        browser_actions,
                        desktop_actions,
                        worker_cost,
                        storage_bytes,
                        total,
                        now,
                        execution_id,
                    ),
                )
            else:
                uid = f"usage_{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO execution_usage
                       (id, execution_id, tenant_id, model_tokens, tool_calls,
                        browser_actions, desktop_actions, worker_cost, storage_bytes,
                        total_cost, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uid,
                        execution_id,
                        tenant_id,
                        model_tokens,
                        tool_calls,
                        browser_actions,
                        desktop_actions,
                        worker_cost,
                        storage_bytes,
                        total,
                        now,
                        now,
                    ),
                )
            await db.commit()
            await emit_global_infrastructure_event(
                "usage_allocated",
                f"Usage allocated for {execution_id}",
                executionId=execution_id,
                tenantId=tenant_id,
                totalCost=total,
            )
            return {"allocated": True, "totalCost": total, "tenantId": tenant_id}
        except Exception as exc:
            logger.debug("Usage allocation skipped: %s", exc)
            return {"allocated": False}

    @staticmethod
    async def forecast_quota(tenant_id: str) -> dict[str, Any]:
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT COALESCE(SUM(total_cost), 0) AS spent, COUNT(*) AS cnt
                   FROM execution_usage WHERE tenant_id = ?""",
                (tenant_id,),
            )
            row = await cursor.fetchone()
            spent = float(row["spent"]) if row else 0.0
            count = int(row["cnt"]) if row else 0
            avg = spent / max(count, 1)
            return {
                "tenantId": tenant_id,
                "totalSpent": spent,
                "executionCount": count,
                "avgCostPerExecution": round(avg, 4),
                "forecastNext10": round(avg * 10, 4),
            }
        except Exception as exc:
            logger.debug("Quota forecast skipped: %s", exc)
            return {"tenantId": tenant_id, "forecastNext10": 0}
