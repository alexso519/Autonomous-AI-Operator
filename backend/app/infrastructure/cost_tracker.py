"""
Execution cost estimation and per-tenant usage accounting.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)

COST_RATES: dict[str, float] = {
    "model_token": 0.000002,
    "tool_call": 0.001,
    "browser_action": 0.005,
    "desktop_action": 0.008,
    "worker_second": 0.0001,
    "storage_kb": 0.00001,
}


class CostTracker:
    """Tracks and estimates runtime execution costs."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_cost_metering

    @staticmethod
    def estimate(
        *,
        model_tokens: int = 0,
        tool_calls: int = 0,
        browser_actions: int = 0,
        desktop_actions: int = 0,
        worker_seconds: float = 0,
        storage_kb: float = 0,
    ) -> float:
        return round(
            model_tokens * COST_RATES["model_token"]
            + tool_calls * COST_RATES["tool_call"]
            + browser_actions * COST_RATES["browser_action"]
            + desktop_actions * COST_RATES["desktop_action"]
            + worker_seconds * COST_RATES["worker_second"]
            + storage_kb * COST_RATES["storage_kb"],
            6,
        )

    @staticmethod
    async def record_cost(
        execution_id: str,
        cost_type: str,
        units: float,
        *,
        tenant_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not CostTracker.enabled():
            return {"recorded": False}
        try:
            rate = COST_RATES.get(cost_type, 0.001)
            estimated = round(units * rate, 6)
            cid = f"cost_{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            await db.execute(
                """INSERT INTO runtime_costs
                   (id, execution_id, tenant_id, cost_type, units, estimated_cost, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    execution_id,
                    tenant_id,
                    cost_type,
                    units,
                    estimated,
                    json.dumps(metadata or {}),
                    now,
                ),
            )
            await db.commit()
            await emit_infrastructure_event(
                execution_id,
                "cost_estimated",
                f"Cost recorded: {cost_type}",
                costType=cost_type,
                estimatedCost=estimated,
            )
            return {"recorded": True, "costId": cid, "estimatedCost": estimated}
        except Exception as exc:
            logger.debug("Cost record skipped: %s", exc)
            return {"recorded": False}

    @staticmethod
    async def get_execution_total(execution_id: str) -> float:
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM runtime_costs WHERE execution_id = ?",
                (execution_id,),
            )
            row = await cursor.fetchone()
            return float(row["total"]) if row else 0.0
        except Exception:
            return 0.0
