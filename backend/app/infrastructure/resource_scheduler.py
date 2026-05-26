"""
Resource-aware scheduling for distributed execution.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event
from app.infrastructure.metrics_aggregator import MetricsAggregator
from app.infrastructure.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)


class ResourceScheduler:
    """Schedules jobs based on worker capacity and pressure."""

    @staticmethod
    async def schedule_with_capacity(
        execution_id: str,
        *,
        priority: int = 5,
        required_capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        from app.infrastructure.distributed_queue import DistributedQueue

        workers = await WorkerRuntime.get_instance().list_workers(include_unhealthy=False)
        available = [
            w
            for w in workers
            if w.active_jobs < w.max_concurrency
            and (
                not required_capabilities
                or set(required_capabilities).issubset(set(w.capabilities))
            )
        ]

        pressure = 1.0 - (len(available) / max(len(workers), 1))
        if pressure > 0.8:
            await emit_global_infrastructure_event(
                "resource_pressure_detected",
                f"High resource pressure: {pressure:.0%}",
                pressureRatio=pressure,
                availableWorkers=len(available),
            )
            MetricsAggregator.set_gauge("runtime.resource.pressure", pressure)

        queue = DistributedQueue.get_instance()
        job = await queue.enqueue(
            execution_id,
            priority=priority,
            required_capabilities=required_capabilities,
        )
        return {
            "jobId": job.job_id,
            "pressure": pressure,
            "availableWorkers": len(available),
        }

    @staticmethod
    async def get_adaptive_concurrency() -> int:
        workers = await WorkerRuntime.get_instance().list_workers()
        if not workers:
            return settings.max_worker_concurrency
        total_capacity = sum(w.max_concurrency - w.active_jobs for w in workers)
        return max(1, min(total_capacity, settings.max_worker_concurrency * len(workers)))
