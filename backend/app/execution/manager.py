"""
Execution runtime safety layer.

Centralizes:
- asyncio.Task registry (execution_id -> task)
- Cancellation propagation
- Per-execution mutual exclusion (re-entrancy guard)
- Thread management for cancellable agent execution
- SSE queue lifecycle coordination
- Orphan detection and cleanup

Architecture:
    ExecutionManager (singleton)
    |- _tasks: dict[str, RegisteredTask]
    |- _locks: dict[str, asyncio.Lock]
    |- _cancel_events: dict[str, asyncio.Event]
    |- register()       # Track a new execution task
    |- cancel()         # Cancel + cleanup an execution
    |- get_status()     # Current execution status
    |- list_active()    # All running execution IDs
    |- cancel_all()     # Cancel everything (shutdown)

Lifecycle:
    register() -> start running -> unregister on completion/error/cancel
                         |
                    cancel() called
                         |
                    +-----------+
                    |  task     |
                    |  cancel   |
                    +-----------+
                         |
                    unregister() + cleanup SSE queue + update DB
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.streaming.event_manager import event_manager

logger = logging.getLogger(__name__)


# === Execution Status ============================================================


class ExecutionStatus(str, Enum):
    """Runtime status of a tracked execution (not the DB status)."""

    RUNNING = "running"
    CANCELLING = "cancelling"  # Cancellation in progress
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    ZOMBIE = "zombie"  # Thread running but task cancelled


# === Registered Task =============================================================


@dataclass
class RegisteredTask:
    """Wraps an asyncio.Task with metadata for lifecycle management."""

    task: asyncio.Task[Any]
    execution_id: str
    workflow_id: str
    status: ExecutionStatus = ExecutionStatus.RUNNING
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    zombie_futures: set[Future[Any]] = field(default_factory=set)
    """Track thread futures that become zombies on cancellation."""

    def mark_cancelling(self) -> None:
        """Transition to cancelling state and signal cancel event."""
        self.status = ExecutionStatus.CANCELLING
        self.cancel_event.set()

    def mark_cancelled(self) -> None:
        """Mark as fully cancelled."""
        self.status = ExecutionStatus.CANCELLED

    def mark_completed(self) -> None:
        """Mark as completed (normal exit)."""
        self.status = ExecutionStatus.COMPLETED

    def mark_failed(self) -> None:
        """Mark as failed (error exit)."""
        self.status = ExecutionStatus.FAILED

    def add_zombie(self, future: Future[Any]) -> None:
        """Track a thread future that outlived cancellation."""
        self.zombie_futures.add(future)

    def is_alive(self) -> bool:
        """Check if the underlying task is still running."""
        return not self.task.done()


# === Execution Lock ==============================================================


class ExecutionLock:
    """
    Per-execution mutual exclusion guard.

    Prevents:
    - Concurrent execution of the same workflow
    - Concurrent approve/reject on the same execution
    - Re-entrant resume_workflow

    Usage:
        guard = ExecutionLock()
        async with guard.acquire(execution_id):
            # Critical section -- only one coroutine at a time
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, str] = {}  # execution_id -> owner_id

    async def acquire(self, execution_id: str, owner_id: str) -> bool:
        """
        Try to acquire the lock for an execution.

        Args:
            execution_id: The execution to lock.
            owner_id: Unique identifier for the caller (e.g., "approve" or "run").

        Returns:
            True if acquired, False if already locked by another owner.

        Raises:
            RuntimeError: If called by the same owner re-entrantly
                          (detected as a bug).
        """
        if execution_id not in self._locks:
            self._locks[execution_id] = asyncio.Lock()

        lock = self._locks[execution_id]

        # Detect re-entrant call by same owner
        if lock.locked() and self._owners.get(execution_id) == owner_id:
            logger.warning(
                "Re-entrant lock detected for execution %s by %s",
                execution_id,
                owner_id,
            )
            return False

        acquired = await lock.acquire()
        if acquired:
            self._owners[execution_id] = owner_id

        return acquired

    def release(self, execution_id: str) -> None:
        """Release the lock for an execution."""
        lock = self._locks.get(execution_id)
        if lock and lock.locked():
            try:
                lock.release()
            except RuntimeError:
                pass
        self._owners.pop(execution_id, None)

    def is_locked(self, execution_id: str) -> bool:
        """Check if an execution is currently locked."""
        lock = self._locks.get(execution_id)
        return lock is not None and lock.locked()

    def cleanup(self, execution_id: str) -> None:
        """Remove all tracking for an execution."""
        self.release(execution_id)
        self._locks.pop(execution_id, None)
        self._owners.pop(execution_id, None)


# === Execution Manager ===========================================================


class ExecutionManager:
    """
    Central runtime safety layer for workflow execution.

    This is a singleton that owns:
    1. Task registry -- maps execution_id -> asyncio.Task
    2. Cancellation signals -- per-execution asyncio.Event
    3. Execution lock -- per-execution mutex
    4. Zombie thread tracking -- threads that survive cancellation

    All execution lifecycle actions MUST go through this manager.
    No fire-and-forget create_task calls outside this class.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._tasks: dict[str, RegisteredTask] = {}
        self._lock = asyncio.Lock()
        self._execution_lock = ExecutionLock()
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent_exec",
        )
        self._orphan_sweep_task: asyncio.Task[None] | None = None
        self._cleanup_registry: set[str] = set()  # executions needing cleanup

    # === Task Registration ======================================================

    async def register(
        self,
        execution_id: str,
        workflow_id: str,
        coro: Any,
        owner_id: str = "run",
    ) -> asyncio.Task[Any] | None:
        """
        Register a new execution task.

        Args:
            execution_id: Unique execution identifier.
            workflow_id: The workflow being executed.
            coro: Coroutine to wrap in a task.
            owner_id: Caller identity for lock ownership.

        Returns:
            The registered asyncio.Task, or None if registration failed
            (duplicate or lock contention).

        Raises:
            RuntimeError: If an active execution with this ID already exists.
        """
        async with self._lock:
            existing = self._tasks.get(execution_id)
            if existing is not None and existing.is_alive():
                logger.error(
                    "Duplicate execution registration: %s (status=%s)",
                    execution_id,
                    existing.status.value,
                )
                raise RuntimeError(
                    f"Execution {execution_id} is already running"
                )

            # Acquire per-execution lock
            acquired = await self._execution_lock.acquire(execution_id, owner_id)
            if not acquired:
                logger.error(
                    "Could not acquire execution lock for %s", execution_id
                )
                return None

            # Create the asyncio Task
            task = asyncio.create_task(
                self._wrap_execution(execution_id, coro),
                name=f"exec-{execution_id[:8]}",
            )

            registered = RegisteredTask(
                task=task,
                execution_id=execution_id,
                workflow_id=workflow_id,
            )
            self._tasks[execution_id] = registered

            logger.info(
                "Registered execution: %s (workflow=%s)",
                execution_id,
                workflow_id,
            )
            return task

    def _get_task(self, execution_id: str) -> RegisteredTask | None:
        """Get registered task metadata (thread-safe, no await)."""
        return self._tasks.get(execution_id)

    async def _wrap_execution(
        self, execution_id: str, coro: Any
    ) -> Any:
        """
        Wrap an execution coroutine with cleanup guarantees.

        This ensures that EVERY execution path triggers cleanup:
        - Normal completion
        - Exception
        - Cancellation
        """
        reg = self._get_task(execution_id)
        try:
            result = await coro
            if reg:
                reg.mark_completed()
            return result
        except asyncio.CancelledError:
            logger.info("Execution %s task was cancelled", execution_id)
            if reg:
                reg.mark_cancelled()
            # Re-raise -- cancellation is expected
            raise
        except Exception as exc:
            logger.exception("Execution %s failed: %s", execution_id, exc)
            if reg:
                reg.mark_failed()
            raise
        finally:
            await self._cleanup_execution(execution_id)

    async def _cleanup_execution(self, execution_id: str) -> None:
        """
        Final cleanup for an execution.

        Called when the execution task completes, fails, or is cancelled.
        Cleanup order:
          1. Disconnect any active SSE subscriber
          2. Release execution lock
          3. Cleanup SSE queue (injects sentinel to end subscriber)
          4. Remove from task registry
        """
        logger.debug("Cleaning up execution %s", execution_id)

        # 1. Disconnect SSE subscriber (wakes up heartbeat loop)
        await event_manager.disconnect_subscriber(execution_id)

        # 2. Release execution lock
        self._execution_lock.cleanup(execution_id)

        # 3. Cleanup SSE queue (injects sentinel to end subscriber)
        await event_manager.cleanup(execution_id)

        # 4. Remove from registry (if still there)
        async with self._lock:
            self._tasks.pop(execution_id, None)

        logger.info("Execution %s fully cleaned up", execution_id)

    # === Cancellation ===========================================================

    async def cancel(
        self, execution_id: str, mark_db_cancelled: bool = True
    ) -> bool:
        """
        Cancel a running execution.

        Args:
            execution_id: The execution to cancel.
            mark_db_cancelled: If True, also update DB status.

        Returns:
            True if cancellation was initiated, False if execution
            not found or already completed.
        """
        async with self._lock:
            reg = self._tasks.get(execution_id)
            if reg is None:
                logger.warning(
                    "Cannot cancel execution %s -- not in registry",
                    execution_id,
                )
                return False

            if reg.status in (
                ExecutionStatus.CANCELLED,
                ExecutionStatus.COMPLETED,
                ExecutionStatus.FAILED,
            ):
                logger.warning(
                    "Cannot cancel execution %s -- already %s",
                    execution_id,
                    reg.status.value,
                )
                return False

            # Signal cancellation
            reg.mark_cancelling()
            logger.info(
                "Cancelling execution %s (workflow=%s)",
                execution_id,
                reg.workflow_id,
            )

        # Persist runtime state for cancellation
        try:
            from app.execution.runtime_state import (
                RuntimeState,
                transition_execution_state,
            )

            await transition_execution_state(
                execution_id=execution_id,
                to_state=RuntimeState.CANCELLED,
                reason="Cancelled by user",
                metadata={"markDbCancelled": mark_db_cancelled},
            )
        except Exception as exc:
            logger.debug(
                "Could not persist cancellation state for %s: %s",
                execution_id,
                exc,
            )

        # Update DB status if requested
        if mark_db_cancelled:
            try:
                from app.database.database import get_db

                db = await get_db()
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    """UPDATE executions
                       SET status = 'cancelled', completed_at = ?, error = ?
                       WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled', 'rejected')""",
                    (now, "Cancelled by user", execution_id),
                )
                await db.commit()
            except Exception as exc:
                logger.warning(
                    "Failed to update DB status for cancelled execution %s: %s",
                    execution_id,
                    exc,
                )

        # Cancel the asyncio task
        reg = self._get_task(execution_id)
        if reg and reg.task and not reg.task.done():
            reg.task.cancel()
            logger.debug("Task.cancel() sent for execution %s", execution_id)

        # Disconnect any subscriber and cleanup SSE queue
        await event_manager.disconnect_subscriber(execution_id)
        await event_manager.cleanup(execution_id)

        # Emit cancelled event via a new queue that exists briefly
        try:
            from app.streaming.event_manager import StreamEvent

            queue_exists = await event_manager.stream_exists(execution_id)
            if not queue_exists:
                await event_manager.create_stream(execution_id)
                await event_manager.emit(
                    execution_id,
                    StreamEvent(
                        type="workflow_cancelled",
                        agent="system",
                        message="Execution cancelled by user",
                    ),
                )
                await event_manager.emit(execution_id, None)
                await event_manager.cleanup(execution_id)
        except Exception as exc:
            logger.debug(
                "Could not emit cancellation event for %s: %s",
                execution_id,
                exc,
            )

        return True

    def is_cancellation_requested(self, execution_id: str) -> bool:
        """Check if cancellation has been requested for an execution."""
        reg = self._get_task(execution_id)
        if reg is None:
            return False
        return reg.cancel_event.is_set()

    def get_cancel_event(self, execution_id: str) -> asyncio.Event | None:
        """Get the cancellation event for an execution, if registered."""
        reg = self._get_task(execution_id)
        if reg is None:
            return None
        return reg.cancel_event

    # === Status & Inspection ====================================================

    def get_status(self, execution_id: str) -> ExecutionStatus | None:
        """Get the current runtime status of an execution."""
        reg = self._get_task(execution_id)
        if reg is None:
            return None
        return reg.status

    def is_running(self, execution_id: str) -> bool:
        """Check if an execution is currently tracked and alive."""
        reg = self._get_task(execution_id)
        if reg is None:
            return False
        return reg.is_alive() and reg.status == ExecutionStatus.RUNNING

    def list_active(self) -> list[dict[str, Any]]:
        """List all active (running/cancelling) executions."""
        active: list[dict[str, Any]] = []
        for eid, reg in self._tasks.items():
            if reg.is_alive() and reg.status in (
                ExecutionStatus.RUNNING,
                ExecutionStatus.CANCELLING,
            ):
                active.append({
                    "executionId": eid,
                    "workflowId": reg.workflow_id,
                    "status": reg.status.value,
                    "startedAt": reg.started_at,
                })
        return active

    def count_active(self) -> int:
        """Return the number of active executions."""
        return len(self.list_active())

    # === Thread Management ======================================================

    async def run_agent_in_thread(
        self,
        execution_id: str,
        fn: Any,
        timeout: float = 300.0,
    ) -> Any:
        """
        Run an agent function in a thread with cancellation support.

        Unlike raw asyncio.to_thread, this:
        - Uses a dedicated ThreadPoolExecutor
        - Tracks zombie threads on cancellation/timeout
        - Checks cancellation signal before starting

        Args:
            execution_id: The execution context.
            fn: Callable to run in thread.
            timeout: Maximum seconds to wait.

        Returns:
            The function result.

        Raises:
            asyncio.TimeoutError: If execution exceeds timeout.
            asyncio.CancelledError: If cancellation was requested.
        """
        # Check cancellation before starting
        if self.is_cancellation_requested(execution_id):
            raise asyncio.CancelledError(
                f"Execution {execution_id} was cancelled before agent started"
            )

        loop = asyncio.get_running_loop()
        future = self._thread_pool.submit(fn)

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, future.result),
                timeout=timeout,
            )
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            # Track as zombie -- thread can't be killed
            reg = self._get_task(execution_id)
            if reg:
                reg.add_zombie(future)
                logger.warning(
                    "Agent thread became zombie for execution %s "
                    "(timeout=%s, future.done=%s)",
                    execution_id,
                    timeout,
                    future.done(),
                )
            raise
        except Exception:
            # Propagate other exceptions
            raise

    def shutdown_thread_pool(self) -> None:
        """Shutdown the thread pool (call during app shutdown)."""
        self._thread_pool.shutdown(wait=False, cancel_futures=True)
        logger.info("Thread pool shut down")

    # === Shutdown ===============================================================

    async def cancel_all(self, reason: str = "Server shutting down") -> int:
        """
        Cancel all active executions.

        Args:
            reason: Reason string for DB status.

        Returns:
            Number of executions cancelled.
        """
        active = self.list_active()
        for info in active:
            eid = info["executionId"]
            try:
                await self.cancel(eid, mark_db_cancelled=True)
                logger.warning("Cancelled %s on shutdown: %s", eid, reason)
            except Exception as exc:
                logger.error(
                    "Failed to cancel %s on shutdown: %s", eid, exc
                )

        shutdown_count = len(active)

        # Final cleanup sweep
        async with self._lock:
            remaining = list(self._tasks.keys())
            for eid in remaining:
                reg = self._tasks.pop(eid, None)
                if reg and not reg.task.done():
                    reg.task.cancel()
                await event_manager.cleanup(eid)
                self._execution_lock.cleanup(eid)

            self._tasks.clear()

        self.shutdown_thread_pool()
        return shutdown_count


# === Singleton ===================================================================

execution_manager = ExecutionManager()
"""Application-wide execution manager instance."""


# === Startup Recovery ============================================================


async def recover_orphaned_executions() -> int:
    """
    On application startup, detect and mark orphaned executions.

    Any execution with status 'running' or 'waiting_approval' was
    left behind by a previous server crash. These cannot be resumed
    and must be marked as orphaned.

    Returns:
        Number of orphaned executions marked.
    """
    from app.database.database import get_db

    db = await get_db()
    cursor = await db.execute(
        """SELECT id, status FROM executions
           WHERE status IN ('running', 'waiting_approval')"""
    )
    orphans = await cursor.fetchall()

    if not orphans:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for row in orphans:
        execution_id = row["id"]
        try:
            await db.execute(
                """UPDATE executions
                   SET status = 'orphaned', completed_at = ?, error = ?
                   WHERE id = ?""",
                (
                    now,
                    "Server restarted during execution -- state lost",
                    execution_id,
                ),
            )
            try:
                from app.execution.runtime_state import (
                    RuntimeState,
                    transition_execution_state,
                )

                await transition_execution_state(
                    execution_id=execution_id,
                    to_state=RuntimeState.ORPHANED,
                    reason="Server restart detected orphaned execution",
                    metadata={"previousStatus": row["status"]},
                )
            except Exception as exc:
                logger.debug(
                    "Could not persist orphaned state for %s: %s",
                    execution_id,
                    exc,
                )

            count += 1
            logger.warning(
                "Marked orphaned execution: %s (%s)",
                execution_id,
                row["status"],
            )
        except Exception as exc:
            logger.error(
                "Failed to mark orphan %s: %s", execution_id, exc
            )

    await db.commit()
    logger.info("Recovered %d orphaned execution(s)", count)
    return count
