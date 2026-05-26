"""
Execution analytics — aggregate metrics and persist snapshots.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


class ExecutionAnalytics:
    """Compute and persist execution analytics snapshots."""

    @classmethod
    async def record_execution_complete(
        cls,
        execution_id: str,
        status: str,
        duration_seconds: float,
        retry_count: int = 0,
        tool_count: int = 0,
        cache_hits: int = 0,
        quality_avg: float | None = None,
        failure_reason: str | None = None,
        model_tier: str | None = None,
    ) -> None:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO execution_metrics
                   (id, execution_id, status, duration_seconds, retry_count,
                    tool_count, cache_hits, quality_avg, failure_reason,
                    model_tier, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex[:16],
                    execution_id,
                    status,
                    duration_seconds,
                    retry_count,
                    tool_count,
                    cache_hits,
                    quality_avg,
                    failure_reason or "",
                    model_tier or "",
                    now,
                ),
            )
            await db.commit()
            await cls.refresh_global_snapshot()
        except Exception as exc:
            logger.warning("Metrics record failed: %s", exc)

    @classmethod
    async def refresh_global_snapshot(cls) -> dict[str, Any]:
        """Recompute aggregate analytics snapshot."""
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT
                     COUNT(*) as total,
                     AVG(duration_seconds) as avg_duration,
                     AVG(retry_count) as avg_retries,
                     AVG(cache_hits) as avg_cache_hits,
                     AVG(quality_avg) as avg_quality,
                     SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failures
                   FROM execution_metrics
                   WHERE created_at > datetime('now', '-30 days')"""
            )
            row = await cursor.fetchone()
            snapshot = {
                "totalExecutions": row["total"] or 0,
                "avgDurationSeconds": round(row["avg_duration"] or 0, 2),
                "avgRetryCount": round(row["avg_retries"] or 0, 2),
                "avgCacheHits": round(row["avg_cache_hits"] or 0, 2),
                "avgQualityScore": round(row["avg_quality"] or 0, 2),
                "failureCount": row["failures"] or 0,
                "failureRate": round(
                    (row["failures"] or 0) / max(row["total"] or 1, 1) * 100, 1
                ),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }

            # Tool usage frequency
            tool_cursor = await db.execute(
                """SELECT tool_name, COUNT(*) as cnt FROM tool_calls
                   GROUP BY tool_name ORDER BY cnt DESC LIMIT 10"""
            )
            tool_rows = await tool_cursor.fetchall()
            snapshot["toolUsage"] = [
                {"tool": r["tool_name"], "count": r["cnt"]} for r in tool_rows
            ]

            # Model routing effectiveness
            route_cursor = await db.execute(
                """SELECT tier, COUNT(*) as cnt FROM model_routing_history
                   GROUP BY tier ORDER BY cnt DESC"""
            )
            route_rows = await route_cursor.fetchall()
            snapshot["modelRouting"] = [
                {"tier": r["tier"], "count": r["cnt"]} for r in route_rows
            ]

            # Failure causes heatmap
            fail_cursor = await db.execute(
                """SELECT failure_reason, COUNT(*) as cnt FROM execution_metrics
                   WHERE failure_reason != '' GROUP BY failure_reason
                   ORDER BY cnt DESC LIMIT 10"""
            )
            fail_rows = await fail_cursor.fetchall()
            snapshot["failureCauses"] = [
                {"reason": r["failure_reason"], "count": r["cnt"]} for r in fail_rows
            ]

            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO analytics_snapshots (id, snapshot_data, created_at)
                   VALUES (?, ?, ?)""",
                (uuid.uuid4().hex[:16], json.dumps(snapshot), now),
            )
            await db.commit()
            return snapshot
        except Exception as exc:
            logger.warning("Analytics snapshot failed: %s", exc)
            return {}

    @classmethod
    async def get_latest_snapshot(cls) -> dict[str, Any]:
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT snapshot_data FROM analytics_snapshots
                   ORDER BY created_at DESC LIMIT 1"""
            )
            row = await cursor.fetchone()
            if row:
                return json.loads(row["snapshot_data"] or "{}")
        except Exception as exc:
            logger.warning("Snapshot fetch failed: %s", exc)
        return await cls.refresh_global_snapshot()

    @classmethod
    async def get_trends(cls, days: int = 14) -> list[dict[str, Any]]:
        """Daily execution performance trends."""
        try:
            db = await get_db()
            cursor = await db.execute(
                f"""SELECT DATE(created_at) as day,
                          COUNT(*) as executions,
                          AVG(duration_seconds) as avg_duration,
                          AVG(retry_count) as avg_retries,
                          AVG(quality_avg) as avg_quality
                   FROM execution_metrics
                   WHERE created_at > datetime('now', '-{int(days)} days')
                   GROUP BY DATE(created_at)
                   ORDER BY day ASC"""
            )
            rows = await cursor.fetchall()
            return [
                {
                    "day": r["day"],
                    "executions": r["executions"],
                    "avgDuration": round(r["avg_duration"] or 0, 1),
                    "avgRetries": round(r["avg_retries"] or 0, 2),
                    "avgQuality": round(r["avg_quality"] or 0, 2),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("Trends fetch failed: %s", exc)
            return []

    @classmethod
    async def get_execution_analytics(cls, execution_id: str) -> dict[str, Any]:
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT * FROM execution_metrics WHERE execution_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (execution_id,),
            )
            m = await cursor.fetchone()

            q_cursor = await db.execute(
                """SELECT AVG(overall_score) as avg_q FROM execution_quality_scores
                   WHERE execution_id = ?""",
                (execution_id,),
            )
            q = await q_cursor.fetchone()

            cache_cursor = await db.execute(
                """SELECT COUNT(*) as hits FROM tool_cache_events
                   WHERE execution_id = ? AND event_type = 'cache_hit'""",
                (execution_id,),
            )
            cache = await cache_cursor.fetchone()

            route_cursor = await db.execute(
                """SELECT model, tier FROM model_routing_history
                   WHERE execution_id = ? ORDER BY created_at ASC""",
                (execution_id,),
            )
            routes = await route_cursor.fetchall()

            return {
                "executionId": execution_id,
                "metrics": dict(m) if m else {},
                "avgQuality": round((q["avg_q"] or 0) if q else 0, 2),
                "cacheHits": cache["hits"] if cache else 0,
                "modelRouting": [
                    {"model": r["model"], "tier": r["tier"]} for r in routes
                ],
            }
        except Exception as exc:
            logger.warning("Execution analytics failed: %s", exc)
            return {"executionId": execution_id}
