"""
Worker registration, heartbeat, and capability matching.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


@dataclass
class WorkerNode:
    worker_id: str
    hostname: str = "local"
    capabilities: list[str] = field(default_factory=list)
    max_concurrency: int = 1
    active_jobs: int = 0
    status: str = "healthy"
    last_heartbeat: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workerId": self.worker_id,
            "hostname": self.hostname,
            "capabilities": self.capabilities,
            "maxConcurrency": self.max_concurrency,
            "activeJobs": self.active_jobs,
            "status": self.status,
            "lastHeartbeat": self.last_heartbeat,
            "metadata": self.metadata,
        }


class WorkerRuntime:
    """Distributed worker lifecycle manager."""

    _instance: WorkerRuntime | None = None

    def __init__(self) -> None:
        self._local_workers: dict[str, WorkerNode] = {}
        self._local_worker_id: str | None = None

    @classmethod
    def get_instance(cls) -> WorkerRuntime:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    async def register(
        self,
        *,
        hostname: str = "local",
        capabilities: list[str] | None = None,
        max_concurrency: int | None = None,
    ) -> WorkerNode:
        worker_id = uuid.uuid4().hex[:16]
        worker = WorkerNode(
            worker_id=worker_id,
            hostname=hostname,
            capabilities=capabilities or ["default"],
            max_concurrency=max_concurrency or settings.max_worker_concurrency,
        )
        self._local_workers[worker_id] = worker
        self._local_worker_id = worker_id

        if settings.enable_distributed_runtime:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO worker_nodes
                   (id, hostname, capabilities, max_concurrency, active_jobs,
                    status, last_heartbeat, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, 'healthy', ?, '{}', ?, ?)""",
                (
                    worker_id,
                    hostname,
                    json.dumps(worker.capabilities),
                    worker.max_concurrency,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()

        await emit_global_infrastructure_event(
            "worker_registered",
            f"Worker {worker_id} registered on {hostname}",
            workerId=worker_id,
            capabilities=worker.capabilities,
        )
        return worker

    async def heartbeat(self, worker_id: str, *, active_jobs: int = 0) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        worker = self._local_workers.get(worker_id)
        if worker:
            worker.last_heartbeat = now
            worker.active_jobs = active_jobs
            worker.status = "healthy"

        if settings.enable_distributed_runtime:
            db = await get_db()
            await db.execute(
                """UPDATE worker_nodes
                   SET last_heartbeat = ?, active_jobs = ?, status = 'healthy', updated_at = ?
                   WHERE id = ?""",
                (now, active_jobs, now, worker_id),
            )
            await db.commit()
        return True

    async def deregister(self, worker_id: str) -> None:
        self._local_workers.pop(worker_id, None)
        if settings.enable_distributed_runtime:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """UPDATE worker_nodes SET status = 'offline', updated_at = ? WHERE id = ?""",
                (now, worker_id),
            )
            await db.commit()

    async def list_workers(self, *, include_unhealthy: bool = False) -> list[WorkerNode]:
        if not settings.enable_distributed_runtime:
            return list(self._local_workers.values())

        db = await get_db()
        if include_unhealthy:
            cursor = await db.execute("SELECT * FROM worker_nodes ORDER BY last_heartbeat DESC")
        else:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(seconds=settings.worker_heartbeat_seconds * 3)
            ).isoformat()
            cursor = await db.execute(
                """SELECT * FROM worker_nodes
                   WHERE status = 'healthy' AND last_heartbeat >= ?
                   ORDER BY last_heartbeat DESC""",
                (cutoff,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_worker(r) for r in rows]

    async def check_health(self) -> list[str]:
        """Return worker IDs marked unhealthy."""
        unhealthy: list[str] = []
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=settings.worker_heartbeat_seconds * 3)
        ).isoformat()

        if not settings.enable_distributed_runtime:
            for wid, worker in self._local_workers.items():
                if worker.last_heartbeat < cutoff:
                    worker.status = "unhealthy"
                    unhealthy.append(wid)
                    await emit_global_infrastructure_event(
                        "worker_unhealthy",
                        f"Worker {wid} missed heartbeat threshold",
                        workerId=wid,
                    )
            return unhealthy

        db = await get_db()
        cursor = await db.execute(
            """SELECT id FROM worker_nodes
               WHERE status = 'healthy' AND last_heartbeat < ?""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            wid = row["id"]
            await db.execute(
                "UPDATE worker_nodes SET status = 'unhealthy', updated_at = ? WHERE id = ?",
                (now, wid),
            )
            unhealthy.append(wid)
            await emit_global_infrastructure_event(
                "worker_unhealthy",
                f"Worker {wid} marked unhealthy",
                workerId=wid,
            )
        if unhealthy:
            await db.commit()
        return unhealthy

    def get_local_worker_id(self) -> str | None:
        return self._local_worker_id

    async def ensure_local_worker(self) -> WorkerNode:
        if self._local_worker_id and self._local_worker_id in self._local_workers:
            return self._local_workers[self._local_worker_id]
        return await self.register()

    @staticmethod
    def _row_to_worker(row: Any) -> WorkerNode:
        return WorkerNode(
            worker_id=row["id"],
            hostname=row["hostname"],
            capabilities=json.loads(row["capabilities"] or "[]"),
            max_concurrency=row["max_concurrency"],
            active_jobs=row["active_jobs"],
            status=row["status"],
            last_heartbeat=row["last_heartbeat"],
            metadata=json.loads(row["metadata"] or "{}"),
        )
