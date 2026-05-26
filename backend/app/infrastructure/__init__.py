"""
Production infrastructure — distributed runtime, persistence, observability, auth, scaling.

All exports use lazy imports to preserve single-node startup performance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.distributed_queue import DistributedQueue
    from app.infrastructure.worker_runtime import WorkerRuntime
    from app.infrastructure.job_dispatcher import JobDispatcher
    from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator


def get_distributed_queue() -> "DistributedQueue":
    from app.infrastructure.distributed_queue import DistributedQueue

    return DistributedQueue.get_instance()


def get_worker_runtime() -> "WorkerRuntime":
    from app.infrastructure.worker_runtime import WorkerRuntime

    return WorkerRuntime.get_instance()


def get_job_dispatcher() -> "JobDispatcher":
    from app.infrastructure.job_dispatcher import JobDispatcher

    return JobDispatcher.get_instance()


def get_infrastructure_coordinator() -> "InfrastructureCoordinator":
    from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

    return InfrastructureCoordinator.get_instance()
