"""
Tests for distributed queue and worker runtime.
Run with: python -m pytest backend/tests/test_distributed_queue.py -v
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


def _reset():
    from app.infrastructure.distributed_queue import DistributedQueue
    from app.infrastructure.worker_runtime import WorkerRuntime
    from app.infrastructure.job_dispatcher import JobDispatcher
    from app.infrastructure.task_leasing import TaskLeasing
    from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

    DistributedQueue.reset_instance()
    WorkerRuntime.reset_instance()
    JobDispatcher.reset_instance()
    TaskLeasing.reset_memory()
    InfrastructureCoordinator.reset_instance()


async def _setup_db():
    from app.database.database import get_db

    await get_db()


async def _teardown_db():
    from app.database.database import close_db

    await close_db()


def test_enqueue_and_claim_job():
    async def run():
        _reset()
        await _setup_db()
        from app.infrastructure.distributed_queue import DistributedQueue, JobStatus
        from app.infrastructure.worker_runtime import WorkerRuntime

        queue = DistributedQueue.get_instance()
        worker = await WorkerRuntime.get_instance().register(capabilities=["default"])
        job = await queue.enqueue("exec_001", "wf_001", priority=3)
        assert job.status == JobStatus.PENDING
        claimed = await queue.claim_next(worker.worker_id, ["default"])
        assert claimed is not None
        assert claimed.job_id == job.job_id
        await _teardown_db()

    asyncio.run(run())


def test_job_retry_on_failure():
    async def run():
        _reset()
        await _setup_db()
        from app.infrastructure.distributed_queue import DistributedQueue, JobStatus

        queue = DistributedQueue.get_instance()
        job = await queue.enqueue("exec_002", "wf_002")
        retried = await queue.fail(job.job_id, error="transient error", retry_delay_seconds=0)
        assert retried is not None
        assert retried.status == JobStatus.RETRY_SCHEDULED
        await _teardown_db()

    asyncio.run(run())


def test_worker_registration():
    async def run():
        _reset()
        await _setup_db()
        from app.infrastructure.worker_runtime import WorkerRuntime

        runtime = WorkerRuntime.get_instance()
        worker = await runtime.register(hostname="test-host", capabilities=["coding"])
        assert worker.worker_id
        assert "coding" in worker.capabilities
        assert await runtime.heartbeat(worker.worker_id, active_jobs=1)
        await _teardown_db()

    asyncio.run(run())


def test_task_leasing():
    async def run():
        _reset()
        await _setup_db()
        from app.infrastructure.task_leasing import TaskLeasing

        lease = await TaskLeasing.acquire("job_1", "worker_1", "exec_1")
        assert lease is not None
        assert await TaskLeasing.renew(lease.lease_id)
        await TaskLeasing.release(lease.lease_id)
        await _teardown_db()

    asyncio.run(run())


def test_queue_depth():
    async def run():
        _reset()
        await _setup_db()
        from app.infrastructure.distributed_queue import DistributedQueue

        queue = DistributedQueue.get_instance()
        await queue.enqueue("exec_a", "wf_a")
        await queue.enqueue("exec_b", "wf_b")
        depth = await queue.queue_depth()
        assert depth.get("pending", 0) >= 2
        await _teardown_db()

    asyncio.run(run())
