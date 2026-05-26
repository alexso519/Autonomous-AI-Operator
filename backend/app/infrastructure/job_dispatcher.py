"""
Job dispatch bridge between distributed queue and execution runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from app.config.settings import settings
from app.infrastructure.distributed_queue import DistributedQueue
from app.infrastructure.task_leasing import TaskLeasing
from app.infrastructure.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)


class JobDispatcher:
    """Dispatches queued jobs to available workers with leasing."""

    _instance: JobDispatcher | None = None

    def __init__(self) -> None:
        self._queue = DistributedQueue.get_instance()
        self._workers = WorkerRuntime.get_instance()
        self._active_leases: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> JobDispatcher:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    async def dispatch_execution(
        self,
        execution_id: str,
        workflow_id: str = "",
        *,
        payload: dict[str, Any] | None = None,
        priority: int = 5,
        required_capabilities: list[str] | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Enqueue an execution for distributed processing."""
        if not settings.enable_distributed_runtime:
            return {
                "mode": "local",
                "executionId": execution_id,
                "dispatched": False,
            }
        merged_payload = dict(payload or {})
        merged_payload.setdefault("tenantId", tenant_id)
        job = await self._queue.enqueue(
            execution_id,
            workflow_id,
            payload=merged_payload,
            priority=priority,
            required_capabilities=required_capabilities,
        )
        return {"mode": "distributed", "job": job.to_dict(), "dispatched": True}

    async def poll_and_execute(
        self,
        worker_id: str,
        capabilities: list[str],
        executor: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any] | None:
        """Claim next job, acquire lease, and run executor callback."""
        job = await self._queue.claim_next(worker_id, capabilities)
        if not job:
            return None

        lease = await TaskLeasing.acquire(
            job.job_id, worker_id, job.execution_id
        )
        if not lease:
            return None

        self._active_leases[job.job_id] = lease.lease_id
        try:
            await executor(job.execution_id, job.payload)
            await self._queue.complete(job.job_id)
            return {"jobId": job.job_id, "status": "completed"}
        except Exception as exc:
            logger.warning("Job %s failed: %s", job.job_id, exc)
            await self._queue.fail(job.job_id, error=str(exc))
            return {"jobId": job.job_id, "status": "failed", "error": str(exc)}
        finally:
            await TaskLeasing.release(lease.lease_id)
            self._active_leases.pop(job.job_id, None)

    async def renew_active_leases(self) -> int:
        count = 0
        for lease_id in list(self._active_leases.values()):
            if await TaskLeasing.renew(lease_id):
                count += 1
        return count

    async def get_dispatch_stats(self) -> dict[str, Any]:
        depth = await self._queue.queue_depth()
        workers = await self._workers.list_workers()
        return {
            "queueDepth": depth,
            "activeWorkers": len(workers),
            "activeLeases": len(self._active_leases),
            "distributedEnabled": settings.enable_distributed_runtime,
        }
