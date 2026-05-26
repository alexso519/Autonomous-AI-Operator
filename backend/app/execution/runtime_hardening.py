"""
Runtime hardening — queue backpressure, priority, dead workflow cleanup.

Single-node only; complements ProductionSafetyGuard without changing execute_workflow().
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    BENCHMARK = 3


@dataclass
class QueuedExecution:
    execution_id: str
    priority: ExecutionPriority = ExecutionPriority.NORMAL
    enqueued_at: float = field(default_factory=time.monotonic)
    objective: str = ""


class RuntimeHardening:
    """Graceful degradation and queue management for single-node runtime."""

    MAX_QUEUE_SIZE = 10
    DEAD_WORKFLOW_SECONDS = 7200  # 2 hours
    BACKPRESSURE_THRESHOLD = 8

    _queue: deque[QueuedExecution] = deque()
    _priorities: dict[str, ExecutionPriority] = {}
    _degraded: bool = False

    @classmethod
    def set_degraded(cls, value: bool, reason: str = "") -> None:
        cls._degraded = value
        if value:
            logger.warning("Runtime entering degraded mode: %s", reason)

    @classmethod
    def is_degraded(cls) -> bool:
        return cls._degraded

    @classmethod
    def enqueue(cls, execution_id: str, priority: ExecutionPriority = ExecutionPriority.NORMAL, objective: str = "") -> bool:
        if len(cls._queue) >= cls.MAX_QUEUE_SIZE:
            logger.warning("Queue full — rejecting execution %s", execution_id)
            return False
        cls._queue.append(QueuedExecution(execution_id, priority, objective=objective))
        cls._priorities[execution_id] = priority
        cls._sort_queue()
        return True

    @classmethod
    def _sort_queue(cls) -> None:
        items = list(cls._queue)
        items.sort(key=lambda q: (-q.priority, q.enqueued_at))
        cls._queue = deque(items)

    @classmethod
    def dequeue(cls) -> QueuedExecution | None:
        return cls._queue.popleft() if cls._queue else None

    @classmethod
    def queue_depth(cls) -> int:
        return len(cls._queue)

    @classmethod
    def check_backpressure(cls, active_count: int) -> tuple[bool, str]:
        depth = cls.queue_depth()
        if depth >= cls.BACKPRESSURE_THRESHOLD:
            return False, "queue_backpressure_limit"
        if active_count >= 3 and depth >= 5:
            cls.set_degraded(True, "high_load_backpressure")
            return True, "degraded_high_load"
        return True, "ok"

    @classmethod
    def get_priority(cls, execution_id: str) -> ExecutionPriority:
        return cls._priorities.get(execution_id, ExecutionPriority.NORMAL)

    @classmethod
    async def cancel_dead_workflows(cls) -> int:
        """Auto-cancel executions running beyond dead threshold."""
        from app.database.database import get_db
        from app.execution.manager import execution_manager

        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT id, started_at FROM executions
                   WHERE status = 'running'
                   AND started_at < datetime('now', ?)""",
                (f"-{cls.DEAD_WORKFLOW_SECONDS} seconds",),
            )
            rows = await cursor.fetchall()
            count = 0
            now = datetime.now(timezone.utc).isoformat()
            for row in rows:
                eid = row["id"]
                await execution_manager.request_cancellation(eid, reason="dead_workflow_timeout")
                await db.execute(
                    """UPDATE executions SET status = 'failed', completed_at = ?,
                       error = ? WHERE id = ?""",
                    (now, "Auto-cancelled: exceeded maximum workflow duration", eid),
                )
                count += 1
            if count:
                await db.commit()
                logger.warning("Auto-cancelled %d dead workflow(s)", count)
            return count
        except Exception as exc:
            logger.warning("Dead workflow cleanup failed: %s", exc)
            return 0

    @classmethod
    async def emit_memory_warning(cls, execution_id: str, emit_fn: Any) -> None:
        from app.execution.production_safety import ProductionSafetyGuard

        mem = ProductionSafetyGuard.check_memory_pressure()
        if mem.degraded:
            await emit_fn(
                execution_id,
                "runtime_warning",
                "system",
                f"Memory pressure detected: {mem.reason}",
                warningType="memory_pressure",
                degraded=True,
            )

    @classmethod
    async def safe_timeout_recovery(
        cls,
        execution_id: str,
        agent_name: str,
        emit_fn: Any,
    ) -> int:
        """Return timeout extension seconds for graceful recovery."""
        from app.execution.execution_stability import RECOVERY_TIMEOUT_EXTENSION

        await emit_fn(
            execution_id,
            "execution_recovery",
            agent_name,
            f"Timeout recovery: extending agent deadline by {RECOVERY_TIMEOUT_EXTENSION}s",
            recoveryType="timeout_extension",
            timeoutExtension=RECOVERY_TIMEOUT_EXTENSION,
        )
        return RECOVERY_TIMEOUT_EXTENSION

    @classmethod
    async def periodic_maintenance(cls) -> dict[str, int]:
        """Run periodic hardening tasks."""
        dead = await cls.cancel_dead_workflows()
        return {"deadWorkflowsCancelled": dead, "queueDepth": cls.queue_depth()}
