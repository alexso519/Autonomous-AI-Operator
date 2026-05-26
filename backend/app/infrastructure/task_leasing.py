"""
Execution leasing with lock renewal for distributed job claiming.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class JobLease:
    lease_id: str
    job_id: str
    worker_id: str
    execution_id: str
    acquired_at: str
    renewed_at: str
    expires_at: str
    released_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaseId": self.lease_id,
            "jobId": self.job_id,
            "workerId": self.worker_id,
            "executionId": self.execution_id,
            "acquiredAt": self.acquired_at,
            "renewedAt": self.renewed_at,
            "expiresAt": self.expires_at,
            "releasedAt": self.released_at,
        }


class TaskLeasing:
    """Manages exclusive leases on distributed jobs."""

    _memory_leases: dict[str, JobLease] = {}

    @classmethod
    async def acquire(
        cls,
        job_id: str,
        worker_id: str,
        execution_id: str,
        *,
        ttl_seconds: int | None = None,
    ) -> JobLease | None:
        ttl = ttl_seconds or settings.worker_heartbeat_seconds * 2
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl)).isoformat()
        lease = JobLease(
            lease_id=uuid.uuid4().hex[:16],
            job_id=job_id,
            worker_id=worker_id,
            execution_id=execution_id,
            acquired_at=now.isoformat(),
            renewed_at=now.isoformat(),
            expires_at=expires,
        )

        if not settings.enable_distributed_runtime:
            cls._memory_leases[job_id] = lease
            return lease

        db = await get_db()
        cursor = await db.execute(
            """SELECT id FROM job_leases
               WHERE job_id = ? AND released_at IS NULL AND expires_at > ?""",
            (job_id, now.isoformat()),
        )
        if await cursor.fetchone():
            return None

        await db.execute(
            """INSERT INTO job_leases
               (id, job_id, worker_id, execution_id, acquired_at, renewed_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                lease.lease_id,
                job_id,
                worker_id,
                execution_id,
                lease.acquired_at,
                lease.renewed_at,
                lease.expires_at,
            ),
        )
        await db.commit()
        return lease

    @classmethod
    async def renew(cls, lease_id: str, *, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds or settings.worker_heartbeat_seconds * 2
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl)).isoformat()

        for lease in cls._memory_leases.values():
            if lease.lease_id == lease_id and lease.released_at is None:
                lease.renewed_at = now.isoformat()
                lease.expires_at = expires
                return True

        if not settings.enable_distributed_runtime:
            return False

        db = await get_db()
        cursor = await db.execute(
            """UPDATE job_leases
               SET renewed_at = ?, expires_at = ?
               WHERE id = ? AND released_at IS NULL""",
            (now.isoformat(), expires, lease_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    @classmethod
    async def release(cls, lease_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for lease in cls._memory_leases.values():
            if lease.lease_id == lease_id:
                lease.released_at = now
                return

        if not settings.enable_distributed_runtime:
            return

        db = await get_db()
        await db.execute(
            "UPDATE job_leases SET released_at = ? WHERE id = ?",
            (now, lease_id),
        )
        await db.commit()

    @classmethod
    async def get_active_lease(cls, job_id: str) -> JobLease | None:
        now = datetime.now(timezone.utc).isoformat()
        for lease in cls._memory_leases.values():
            if (
                lease.job_id == job_id
                and lease.released_at is None
                and lease.expires_at > now
            ):
                return lease

        if not settings.enable_distributed_runtime:
            return None

        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM job_leases
               WHERE job_id = ? AND released_at IS NULL AND expires_at > ?
               ORDER BY acquired_at DESC LIMIT 1""",
            (job_id, now),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return JobLease(
            lease_id=row["id"],
            job_id=row["job_id"],
            worker_id=row["worker_id"],
            execution_id=row["execution_id"],
            acquired_at=row["acquired_at"],
            renewed_at=row["renewed_at"],
            expires_at=row["expires_at"],
            released_at=row["released_at"],
        )

    @classmethod
    def reset_memory(cls) -> None:
        cls._memory_leases.clear()
