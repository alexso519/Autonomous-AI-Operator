"""
Operator dashboard API — runtime health, analytics, execution controls.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database.database import get_db
from app.execution.execution_analytics import ExecutionAnalytics
from app.execution.execution_stability import ExecutionStabilityMonitor
from app.execution.manager import execution_manager
from app.execution.production_safety import ProductionSafetyGuard

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/operator", tags=["operator"])


@router.get("/dashboard")
async def get_operator_dashboard() -> dict[str, Any]:
    """Aggregate operator view: active runs, health, analytics."""
    db = await get_db()
    active_count = execution_manager.count_active()

    cursor = await db.execute(
        """SELECT id, workflow_name, status, started_at FROM executions
           WHERE status IN ('running', 'waiting_approval')
           ORDER BY started_at DESC LIMIT 20"""
    )
    active_rows = await cursor.fetchall()
    active_executions = [
        {
            "id": r["id"],
            "workflowName": r["workflow_name"],
            "status": r["status"],
            "startedAt": r["started_at"],
            "stalled": ExecutionStabilityMonitor.check_stalled(r["id"]).is_stalled,
        }
        for r in active_rows
    ]

    stalled = [e for e in active_executions if e["stalled"]]

    metrics_cursor = await db.execute(
        """SELECT AVG(duration_seconds) as avg_dur,
                  AVG(retry_count) as avg_retry,
                  AVG(cache_hits) as avg_cache,
                  AVG(quality_avg) as avg_quality,
                  COUNT(*) as total
           FROM execution_metrics
           WHERE created_at > datetime('now', '-7 days')"""
    )
    m = await metrics_cursor.fetchone()

    analytics = await ExecutionAnalytics.get_latest_snapshot()
    safety = ProductionSafetyGuard.pre_start_check(active_count)

    return {
        "runtimeHealth": {
            "status": "degraded" if safety.degraded else "healthy",
            "activeExecutions": active_count,
            "maxConcurrent": ProductionSafetyGuard.MAX_CONCURRENT_EXECUTIONS,
            "reason": safety.reason or None,
        },
        "activeExecutions": active_executions,
        "stalledExecutions": stalled,
        "metrics": {
            "avgDurationSeconds": round(m["avg_dur"] or 0, 1) if m else 0,
            "avgRetryCount": round(m["avg_retry"] or 0, 2) if m else 0,
            "avgCacheHitRate": round(m["avg_cache"] or 0, 2) if m else 0,
            "avgQualityScore": round(m["avg_quality"] or 0, 2) if m else 0,
            "executionsLast7Days": m["total"] if m else 0,
        },
        "analytics": analytics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/analytics/trends")
async def get_analytics_trends(days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    trends = await ExecutionAnalytics.get_trends(days=days)
    snapshot = await ExecutionAnalytics.get_latest_snapshot()
    return {"trends": trends, "snapshot": snapshot}


@router.get("/analytics/failures")
async def get_failure_heatmap() -> dict[str, Any]:
    snapshot = await ExecutionAnalytics.get_latest_snapshot()
    return {"failureCauses": snapshot.get("failureCauses", [])}


@router.get("/executions/{execution_id}/analytics")
async def get_execution_analytics(execution_id: str) -> dict[str, Any]:
    data = await ExecutionAnalytics.get_execution_analytics(execution_id)
    if not data.get("metrics") and not data.get("modelRouting"):
        cursor = await (await get_db()).execute(
            "SELECT id FROM executions WHERE id = ?", (execution_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(404, "Execution not found")
    return data


@router.get("/executions/{execution_id}/routing")
async def get_routing_history(execution_id: str) -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT model, tier, event_type, agent_name, reasons, created_at
           FROM model_routing_history WHERE execution_id = ?
           ORDER BY created_at ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "model": r["model"],
            "tier": r["tier"],
            "eventType": r["event_type"],
            "agentName": r["agent_name"],
            "reasons": json.loads(r["reasons"] or "[]"),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


@router.get("/executions/{execution_id}/spawn-plan")
async def get_spawn_plan(execution_id: str) -> dict[str, Any]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT mode, agent_count, task_type, rationale, plan_data, created_at
           FROM agent_spawn_plans WHERE execution_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(404, "Spawn plan not found")
    return {
        "mode": row["mode"],
        "agentCount": row["agent_count"],
        "taskType": row["task_type"],
        "rationale": json.loads(row["rationale"] or "[]"),
        "plan": json.loads(row["plan_data"] or "{}"),
        "createdAt": row["created_at"],
    }


@router.post("/executions/{execution_id}/retry")
async def retry_execution(execution_id: str) -> dict[str, Any]:
    """Re-run workflow from a completed/failed execution."""
    from app.routes.executions import rerun_execution

    return await rerun_execution(execution_id)


@router.get("/performance/aggregate")
async def get_aggregate_performance(days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    from app.execution.runtime_performance import RuntimePerformanceTracker

    return await RuntimePerformanceTracker.get_aggregate_performance(days=days)


@router.get("/executions/{execution_id}/performance")
async def get_execution_performance(execution_id: str) -> dict[str, Any]:
    from app.execution.runtime_performance import RuntimePerformanceTracker

    data = await RuntimePerformanceTracker.get_execution_performance(execution_id)
    if not data:
        cursor = await (await get_db()).execute(
            "SELECT id FROM executions WHERE id = ?", (execution_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(404, "Execution not found")
    return data


@router.post("/executions/{execution_id}/replay")
async def replay_execution(execution_id: str) -> dict[str, Any]:
    """Return persisted logs for replay (read-only)."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT event_type, timestamp, agent, message, data
           FROM execution_event_logs WHERE execution_id = ?
           ORDER BY timestamp ASC, id ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    if not rows:
        raise HTTPException(404, "No replay data for execution")
    return {
        "executionId": execution_id,
        "events": [
            {
                "type": r["event_type"],
                "timestamp": r["timestamp"],
                "agent": r["agent"],
                "message": r["message"],
                "data": json.loads(r["data"] or "{}"),
            }
            for r in rows
        ],
    }
