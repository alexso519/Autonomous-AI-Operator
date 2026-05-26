"""
Horizontal worker scaling hooks and adaptive concurrency.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.distributed_queue import DistributedQueue
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event
from app.infrastructure.metrics_aggregator import MetricsAggregator
from app.infrastructure.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)


class RuntimeScaler:
    """Scaling recommendations and worker pool management."""

    @staticmethod
    async def evaluate_scaling() -> dict[str, Any]:
        queue = DistributedQueue.get_instance()
        depth = await queue.queue_depth()
        workers = await WorkerRuntime.get_instance().list_workers()
        pending = depth.get("pending", 0)
        worker_count = len(workers)

        recommended = worker_count
        action = "none"

        if pending > worker_count * 5 and worker_count < 20:
            recommended = min(worker_count + 2, 20)
            action = "scale_up"
        elif pending == 0 and worker_count > 1:
            recommended = max(1, worker_count - 1)
            action = "scale_down"

        if action != "none":
            await emit_global_infrastructure_event(
                "runtime_scaled",
                f"Scaling recommendation: {action} to {recommended} workers",
                currentWorkers=worker_count,
                recommendedWorkers=recommended,
                queuePending=pending,
                action=action,
            )
            MetricsAggregator.increment("runtime.scale.events")

        return {
            "currentWorkers": worker_count,
            "recommendedWorkers": recommended,
            "queuePending": pending,
            "action": action,
            "maxConcurrency": settings.max_worker_concurrency,
        }

    @staticmethod
    async def set_concurrency_limit(limit: int) -> dict[str, Any]:
        effective = max(1, min(limit, 100))
        MetricsAggregator.set_gauge("runtime.concurrency.limit", float(effective))
        return {"concurrencyLimit": effective}
