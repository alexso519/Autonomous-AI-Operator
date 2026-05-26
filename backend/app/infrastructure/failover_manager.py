"""
Failover manager — reassign execution on worker failure.
"""

from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.distributed_queue import DistributedQueue
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event
from app.infrastructure.task_leasing import TaskLeasing
from app.infrastructure.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)


class FailoverManager:
    """Handles worker failure and job reassignment."""

    @staticmethod
    async def run_failover_cycle() -> dict[str, Any]:
        workers = WorkerRuntime.get_instance()
        unhealthy = await workers.check_health()
        queue = DistributedQueue.get_instance()
        reassigned = await queue.reassign_stalled()

        if unhealthy or reassigned:
            await emit_global_infrastructure_event(
                "failover_triggered",
                f"Failover cycle: {len(unhealthy)} unhealthy, {reassigned} reassigned",
                unhealthyWorkers=unhealthy,
                reassignedJobs=reassigned,
            )

        return {
            "unhealthyWorkers": unhealthy,
            "reassignedJobs": reassigned,
        }

    @staticmethod
    async def drain_worker(worker_id: str) -> dict[str, Any]:
        await WorkerRuntime.get_instance().deregister(worker_id)
        return {"workerId": worker_id, "status": "draining"}

    @staticmethod
    async def release_stale_leases() -> int:
        queue = DistributedQueue.get_instance()
        return await queue.reassign_stalled()
