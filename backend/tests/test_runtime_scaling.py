"""
Tests for runtime scaling and failover.
Run with: python -m pytest backend/tests/test_runtime_scaling.py -v
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
    from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

    DistributedQueue.reset_instance()
    WorkerRuntime.reset_instance()
    InfrastructureCoordinator.reset_instance()


def test_runtime_scaler_evaluate():
    async def run():
        _reset()
        from app.database.database import get_db, close_db

        await get_db()
        from app.infrastructure.runtime_scaler import RuntimeScaler
        from app.infrastructure.worker_runtime import WorkerRuntime

        await WorkerRuntime.get_instance().register()
        result = await RuntimeScaler.evaluate_scaling()
        assert "currentWorkers" in result
        await close_db()

    asyncio.run(run())


def test_failover_cycle():
    async def run():
        _reset()
        from app.database.database import get_db, close_db

        await get_db()
        from app.infrastructure.failover_manager import FailoverManager

        result = await FailoverManager.run_failover_cycle()
        assert "reassignedJobs" in result
        await close_db()

    asyncio.run(run())


def test_degraded_mode():
    async def run():
        from app.infrastructure.degraded_mode_manager import DegradedModeManager

        await DegradedModeManager.disable()
        await DegradedModeManager.enable("test pressure")
        assert DegradedModeManager.is_degraded()
        await DegradedModeManager.disable()

    asyncio.run(run())


def test_resource_scheduler():
    async def run():
        _reset()
        from app.database.database import get_db, close_db

        await get_db()
        from app.infrastructure.resource_scheduler import ResourceScheduler
        from app.infrastructure.worker_runtime import WorkerRuntime

        await WorkerRuntime.get_instance().register(capabilities=["default"])
        result = await ResourceScheduler.schedule_with_capacity("exec_scale1")
        assert "jobId" in result
        await close_db()

    asyncio.run(run())
