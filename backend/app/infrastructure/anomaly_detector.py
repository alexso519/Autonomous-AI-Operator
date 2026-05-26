"""
Infrastructure anomaly detection — retry storms, queue spikes, worker degradation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detects runtime infrastructure anomalies."""

    _retry_counts: dict[str, list[float]] = defaultdict(list)
    _queue_history: list[tuple[float, int]] = []

    @staticmethod
    def enabled() -> bool:
        return settings.enable_infra_intelligence

    @staticmethod
    async def record_retry(execution_id: str) -> dict[str, Any] | None:
        if not AnomalyDetector.enabled():
            return None
        now = datetime.now(timezone.utc).timestamp()
        AnomalyDetector._retry_counts[execution_id].append(now)
        recent = [
            t for t in AnomalyDetector._retry_counts[execution_id] if now - t < 60
        ]
        AnomalyDetector._retry_counts[execution_id] = recent
        if len(recent) >= 5:
            await emit_global_infrastructure_event(
                "anomaly_detected",
                f"Retry storm on {execution_id}",
                executionId=execution_id,
                retryCount=len(recent),
                anomalyType="retry_storm",
            )
            return {"anomalyType": "retry_storm", "executionId": execution_id, "count": len(recent)}
        return None

    @staticmethod
    async def check_queue_pressure(pending: int) -> dict[str, Any] | None:
        if not AnomalyDetector.enabled():
            return None
        now = datetime.now(timezone.utc).timestamp()
        AnomalyDetector._queue_history.append((now, pending))
        AnomalyDetector._queue_history = [
            (t, p) for t, p in AnomalyDetector._queue_history if now - t < 300
        ]
        if pending > 100:
            await emit_global_infrastructure_event(
                "anomaly_detected",
                f"Queue pressure spike: {pending}",
                pending=pending,
                anomalyType="queue_spike",
            )
            return {"anomalyType": "queue_spike", "pending": pending}
        return None

    @staticmethod
    async def scan_metrics() -> list[dict[str, Any]]:
        anomalies: list[dict[str, Any]] = []
        if not AnomalyDetector.enabled():
            return anomalies
        try:
            from app.infrastructure.distributed_queue import DistributedQueue

            depth = await DistributedQueue.get_instance().queue_depth()
            pending = depth.get("pending", 0)
            result = await AnomalyDetector.check_queue_pressure(pending)
            if result:
                anomalies.append(result)
        except Exception as exc:
            logger.debug("Anomaly scan skipped: %s", exc)
        return anomalies
