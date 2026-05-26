"""
Distributed execution queue with Redis, SQLite, and in-memory backends.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLED = "cancelled"


@dataclass
class DistributedJob:
    job_id: str
    execution_id: str
    workflow_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 5
    status: JobStatus = JobStatus.PENDING
    required_capabilities: list[str] = field(default_factory=list)
    worker_id: str = ""
    retry_count: int = 0
    max_retries: int = 3
    available_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "executionId": self.execution_id,
            "workflowId": self.workflow_id,
            "payload": self.payload,
            "priority": self.priority,
            "status": self.status.value,
            "requiredCapabilities": self.required_capabilities,
            "workerId": self.worker_id,
            "retryCount": self.retry_count,
            "maxRetries": self.max_retries,
            "availableAt": self.available_at,
            "createdAt": self.created_at,
        }


class DistributedQueue:
    """Durable priority queue with pluggable backends."""

    _instance: DistributedQueue | None = None

    def __init__(self) -> None:
        self._backend = self._resolve_backend()
        self._memory_jobs: dict[str, DistributedJob] = {}
        self._memory_order: list[str] = []

    @classmethod
    def get_instance(cls) -> DistributedQueue:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _resolve_backend(self) -> str:
        if not settings.enable_distributed_runtime:
            return "memory"
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis  # noqa: F401

                return "redis"
            except ImportError:
                logger.warning("redis package unavailable — falling back to sqlite")
        return "sqlite"

    async def enqueue(
        self,
        execution_id: str,
        workflow_id: str = "",
        *,
        payload: dict[str, Any] | None = None,
        priority: int = 5,
        required_capabilities: list[str] | None = None,
        delay_seconds: int = 0,
    ) -> DistributedJob:
        now = datetime.now(timezone.utc)
        available_at = (
            now + timedelta(seconds=delay_seconds)
        ).isoformat()
        job = DistributedJob(
            job_id=uuid.uuid4().hex[:16],
            execution_id=execution_id,
            workflow_id=workflow_id,
            payload=payload or {},
            priority=priority,
            required_capabilities=required_capabilities or [],
            available_at=available_at,
        )
        await self._persist_job(job)
        await emit_global_infrastructure_event(
            "job_dispatched",
            f"Job {job.job_id} enqueued for execution {execution_id}",
            executionId=execution_id,
            jobId=job.job_id,
            priority=priority,
        )
        return job

    async def claim_next(
        self,
        worker_id: str,
        capabilities: list[str] | None = None,
    ) -> DistributedJob | None:
        caps = set(capabilities or [])
        job = await self._claim_from_backend(worker_id, caps)
        if job:
            await emit_global_infrastructure_event(
                "job_claimed",
                f"Worker {worker_id} claimed job {job.job_id}",
                executionId=job.execution_id,
                jobId=job.job_id,
                workerId=worker_id,
            )
        return job

    async def complete(self, job_id: str, *, result: dict[str, Any] | None = None) -> None:
        await self._update_status(job_id, JobStatus.COMPLETED, result=result)

    async def fail(
        self,
        job_id: str,
        *,
        error: str = "",
        retry_delay_seconds: int = 30,
    ) -> DistributedJob | None:
        job = await self.get_job(job_id)
        if not job:
            return None
        if job.retry_count < job.max_retries:
            job.retry_count += 1
            job.status = JobStatus.RETRY_SCHEDULED
            job.worker_id = ""
            job.available_at = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
            ).isoformat()
            await self._persist_job(job)
            await self._record_retry(job, error)
            await emit_global_infrastructure_event(
                "distributed_retry_scheduled",
                f"Job {job_id} retry #{job.retry_count} scheduled",
                executionId=job.execution_id,
                jobId=job_id,
                retryCount=job.retry_count,
                error=error,
            )
            return job
        await self._update_status(job_id, JobStatus.FAILED, error=error)
        return None

    async def reassign_stalled(self, lease_timeout_seconds: int | None = None) -> int:
        timeout = lease_timeout_seconds or settings.worker_heartbeat_seconds * 3
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout)
        ).isoformat()
        db = await get_db()
        cursor = await db.execute(
            """SELECT j.id, j.execution_id
               FROM distributed_jobs j
               LEFT JOIN job_leases l ON l.job_id = j.id AND l.released_at IS NULL
               WHERE j.status IN ('claimed', 'running')
                 AND (l.renewed_at IS NULL OR l.renewed_at < ?)""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            job_id = row["id"]
            await db.execute(
                """UPDATE distributed_jobs
                   SET status = 'pending', worker_id = '', updated_at = ?
                   WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
            await db.execute(
                """UPDATE job_leases SET released_at = ? WHERE job_id = ? AND released_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
            await emit_global_infrastructure_event(
                "job_reassigned",
                f"Stalled job {job_id} reassigned",
                jobId=job_id,
                executionId=row["execution_id"],
            )
            count += 1
        if count:
            await db.commit()
        return count

    async def get_job(self, job_id: str) -> DistributedJob | None:
        if self._backend == "memory":
            return self._memory_jobs.get(job_id)
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM distributed_jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_job(row) if row else None

    async def queue_depth(self) -> dict[str, int]:
        if self._backend == "memory":
            pending = sum(
                1 for j in self._memory_jobs.values() if j.status == JobStatus.PENDING
            )
            return {"pending": pending, "total": len(self._memory_jobs)}
        db = await get_db()
        cursor = await db.execute(
            """SELECT status, COUNT(*) as cnt FROM distributed_jobs GROUP BY status"""
        )
        rows = await cursor.fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats

    async def _claim_from_backend(
        self, worker_id: str, capabilities: set[str]
    ) -> DistributedJob | None:
        if self._backend == "memory":
            return self._claim_memory(worker_id, capabilities)
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """SELECT * FROM distributed_jobs
               WHERE status = 'pending' AND available_at <= ?
               ORDER BY priority ASC, created_at ASC
               LIMIT 20""",
            (now,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            job = self._row_to_job(row)
            if job.required_capabilities and not set(job.required_capabilities).issubset(
                capabilities
            ):
                continue
            await db.execute(
                """UPDATE distributed_jobs
                   SET status = 'claimed', worker_id = ?, updated_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (worker_id, now, job.job_id),
            )
            await db.commit()
            job.status = JobStatus.CLAIMED
            job.worker_id = worker_id
            return job
        return None

    def _claim_memory(
        self, worker_id: str, capabilities: set[str]
    ) -> DistributedJob | None:
        now = datetime.now(timezone.utc).isoformat()
        for job_id in sorted(
            self._memory_order,
            key=lambda jid: (
                self._memory_jobs[jid].priority,
                self._memory_jobs[jid].created_at,
            ),
        ):
            job = self._memory_jobs[job_id]
            if job.status != JobStatus.PENDING:
                continue
            if job.available_at > now:
                continue
            if job.required_capabilities and not set(job.required_capabilities).issubset(
                capabilities
            ):
                continue
            job.status = JobStatus.CLAIMED
            job.worker_id = worker_id
            return job
        return None

    async def _persist_job(self, job: DistributedJob) -> None:
        if self._backend == "memory":
            self._memory_jobs[job.job_id] = job
            if job.job_id not in self._memory_order:
                self._memory_order.append(job.job_id)
            return
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO distributed_jobs
               (id, execution_id, workflow_id, payload, priority, status,
                required_capabilities, worker_id, retry_count, max_retries,
                available_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 status = excluded.status,
                 worker_id = excluded.worker_id,
                 retry_count = excluded.retry_count,
                 available_at = excluded.available_at,
                 updated_at = excluded.updated_at""",
            (
                job.job_id,
                job.execution_id,
                job.workflow_id,
                json.dumps(job.payload),
                job.priority,
                job.status.value,
                json.dumps(job.required_capabilities),
                job.worker_id,
                job.retry_count,
                job.max_retries,
                job.available_at,
                job.created_at,
                now,
            ),
        )
        await db.commit()

    async def _update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        if self._backend == "memory" and job_id in self._memory_jobs:
            job = self._memory_jobs[job_id]
            job.status = status
            if result or error:
                job.payload.update({"result": result or {}, "error": error})
            return
        db = await get_db()
        cursor = await db.execute(
            "SELECT payload FROM distributed_jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        payload = json.loads(row["payload"] or "{}") if row else {}
        payload.update({"result": result or {}, "error": error})
        await db.execute(
            """UPDATE distributed_jobs
               SET status = ?, updated_at = ?, payload = ?
               WHERE id = ?""",
            (
                status.value,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(payload),
                job_id,
            ),
        )
        await db.commit()

    async def _record_retry(self, job: DistributedJob, error: str) -> None:
        if self._backend == "memory":
            return
        db = await get_db()
        await db.execute(
            """INSERT INTO job_retry_history
               (id, job_id, execution_id, retry_number, error, scheduled_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:16],
                job.job_id,
                job.execution_id,
                job.retry_count,
                error,
                job.available_at,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    @staticmethod
    def _row_to_job(row: Any) -> DistributedJob:
        return DistributedJob(
            job_id=row["id"],
            execution_id=row["execution_id"],
            workflow_id=row["workflow_id"] or "",
            payload=json.loads(row["payload"] or "{}"),
            priority=row["priority"],
            status=JobStatus(row["status"]),
            required_capabilities=json.loads(row["required_capabilities"] or "[]"),
            worker_id=row["worker_id"] or "",
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            available_at=row["available_at"],
            created_at=row["created_at"],
        )
