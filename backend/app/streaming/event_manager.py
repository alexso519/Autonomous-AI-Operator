"""
SSE Event Manager -- Stabilized.

Each execution gets its own asyncio.Queue.
The execution engine pushes typed events into the queue.
The SSE route pulls events from the queue and streams them to the frontend.

Stabilization features:
  +----------------------------------------------+
  | 1. Heartbeat task per subscriber              |
  | 2. Stream ownership (one subscriber at a time)|
  | 3. Idle timeout (30s between events)          |
  | 4. Subscriber tracking                        |
  | 5. Historical replay (since= param)           |
  | 6. Orphan sweep (periodic cleanup)            |
  | 7. Deterministic cleanup (sentinel on cleanup)|
  +----------------------------------------------+

No Redis, No Kafka, No WebSockets -- just asyncio.Queue + SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncGenerator, Any

logger = logging.getLogger(__name__)


# -- Constants ------------------------------------------------

HEARTBEAT_INTERVAL = 10.0
SUBSCRIBE_TIMEOUT = 30.0
IDLE_CLEANUP_TIMEOUT = 60.0
ORPHAN_SWEEP_INTERVAL = 300.0


# -- Typed Stream Events --------------------------------------


@dataclass
class StreamEvent:
    """A single event emitted during workflow execution."""

    type: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    agent: str = "system"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "message": self.message,
            "data": self.data,
        }


# -- Queue Wrapper with Subscriber Tracking -------------------


@dataclass
class StreamQueue:
    """Wraps an asyncio.Queue with subscriber + lifecycle metadata."""

    queue: asyncio.Queue[StreamEvent | None]
    subscriber_id: str | None = None
    subscriber_event: asyncio.Event = field(default_factory=asyncio.Event)
    last_activity: float = field(default_factory=lambda: time.monotonic())
    created_at: float = field(default_factory=lambda: time.monotonic())
    heartbeat_task: asyncio.Task[None] | None = None

    @property
    def has_subscriber(self) -> bool:
        return self.subscriber_event.is_set()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def assign_subscriber(self, subscriber_id: str) -> None:
        self.subscriber_id = subscriber_id
        self.subscriber_event.set()
        self.touch()

    def remove_subscriber(self) -> None:
        self.subscriber_id = None
        self.subscriber_event.clear()
        self.touch()


# -- Event Manager --------------------------------------------


class EventManager:
    """
    Manages per-execution asyncio.Queue instances with heartbeat,
    stream ownership, and automatic cleanup.

    Each execution gets its own bounded queue (maxsize=1024).
    Only one subscriber per queue at a time.
    Heartbeat tasks keep connections alive during idle periods.
    Orphan sweep cleans up abandoned queues periodically.
    """

    def __init__(self) -> None:
        self._queues: dict[str, StreamQueue] = {}
        self._lock = asyncio.Lock()
        self._orphan_sweep_task: asyncio.Task[None] | None = None

    # -- Queue Lifecycle --------------------------------------

    async def create_stream(self, execution_id: str) -> None:
        """Create a new event queue, replacing any existing one."""
        async with self._lock:
            old = self._queues.pop(execution_id, None)
            if old is not None:
                if old.heartbeat_task is not None and not old.heartbeat_task.done():
                    old.heartbeat_task.cancel()
                logger.warning(
                    "Replacing existing queue for execution %s", execution_id
                )
            self._queues[execution_id] = StreamQueue(
                queue=asyncio.Queue(maxsize=1024)
            )
            logger.debug("Created stream for execution %s", execution_id)

    async def stream_exists(self, execution_id: str) -> bool:
        """Check if a stream queue exists for this execution."""
        async with self._lock:
            return execution_id in self._queues

    # -- Event Emission ---------------------------------------

    async def emit(
        self, execution_id: str, event: StreamEvent | None
    ) -> None:
        """Push an event into the execution's queue."""
        async with self._lock:
            sq = self._queues.get(execution_id)
            if sq is None:
                logger.warning(
                    "No queue for execution %s -- dropping event: %s",
                    execution_id,
                    event.type if event else "sentinel",
                )
                return

        try:
            await sq.queue.put(event)
            sq.touch()
            if event is not None:
                logger.debug(
                    "Emitted event: %s | agent=%s", event.type, event.agent
                )
        except asyncio.QueueFull:
            logger.error(
                "Queue full for execution %s -- dropping event: %s",
                execution_id,
                event.type if event else "sentinel",
            )

    async def emit_nowait(
        self, execution_id: str, event: StreamEvent
    ) -> None:
        """Push an event without awaiting."""
        async with self._lock:
            sq = self._queues.get(execution_id)
            if sq is None:
                return
        try:
            sq.queue.put_nowait(event)
            sq.touch()
        except asyncio.QueueFull:
            await sq.queue.put(event)

    # -- Heartbeat Task ---------------------------------------

    async def _heartbeat_loop(
        self, execution_id: str, subscriber_id: str
    ) -> None:
        """
        Background task emitting stream_heartbeat every interval.

        Keeps SSE alive during idle periods. Heartbeats reset
        the subscriber's timeout timer.
        """
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                # Verify we're still the active subscriber
                async with self._lock:
                    sq = self._queues.get(execution_id)
                    if sq is None or sq.subscriber_id != subscriber_id:
                        return

                heartbeat = StreamEvent(
                    type="stream_heartbeat",
                    agent="system",
                    message="",
                    data={"subscriber_id": subscriber_id},
                )
                try:
                    await sq.queue.put(heartbeat)
                    sq.touch()
                except asyncio.QueueFull:
                    pass
        except asyncio.CancelledError:
            logger.debug(
                "Heartbeat cancelled for execution %s (subscriber=%s)",
                execution_id,
                subscriber_id,
            )

    # -- Historical Event Replay ------------------------------

    async def get_historical_events(
        self,
        execution_id: str,
        since_timestamp: str | None = None,
        limit: int = 500,
    ) -> list[StreamEvent]:
        """
        Fetch historical events from execution_event_logs.

        Used by the stream route's `since` parameter for replay
        after reconnect.
        """
        try:
            from app.database.database import get_db

            db = await get_db()

            if since_timestamp:
                cursor = await db.execute(
                    """SELECT event_type, timestamp, agent, message, data
                       FROM execution_event_logs
                       WHERE execution_id = ? AND timestamp > ?
                       ORDER BY timestamp ASC
                       LIMIT ?""",
                    (execution_id, since_timestamp, limit),
                )
            else:
                cursor = await db.execute(
                    """SELECT event_type, timestamp, agent, message, data
                       FROM execution_event_logs
                       WHERE execution_id = ?
                       ORDER BY timestamp ASC
                       LIMIT ?""",
                    (execution_id, limit),
                )

            rows = await cursor.fetchall()
            events: list[StreamEvent] = []
            for row in rows:
                d = dict(row)
                data = {}
                try:
                    data = json.loads(d.get("data", "{}"))
                except (json.JSONDecodeError, TypeError):
                    data = {}
                events.append(
                    StreamEvent(
                        type=d["event_type"],
                        timestamp=d["timestamp"],
                        agent=d.get("agent", "system"),
                        message=d.get("message", ""),
                        data=data,
                    )
                )
            return events
        except Exception as exc:
            logger.error(
                "Failed to fetch historical events for %s: %s",
                execution_id,
                exc,
            )
            return []

    # -- Subscription -----------------------------------------

    async def subscribe(
        self,
        execution_id: str,
        subscriber_id: str,
        timeout: float = SUBSCRIBE_TIMEOUT,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Async generator yielding events from the execution's queue.

        Args:
            execution_id: Execution to subscribe to.
            subscriber_id: Unique ID for this subscriber.
            timeout: Max seconds between events before closing.
                     Heartbeats reset this timer.

        Yields:
            StreamEvent instances including stream_heartbeat.

        Ownership:
            Only one subscriber per queue. A new call replaces
            the old subscriber (cancelling its heartbeat).

        Generator ends when:
            - None sentinel received (execution finished)
            - Timeout reached (no events or heartbeats)
            - Queue cleaned up (sentinel injected)
            - Subscriber replaced by new connection
        """
        sq: StreamQueue | None = None

        async with self._lock:
            sq = self._queues.get(execution_id)
            if sq is None:
                logger.warning(
                    "No queue for execution %s -- stream unavailable",
                    execution_id,
                )
                return

            # Disconnect old subscriber if any
            if sq.has_subscriber:
                old_id = sq.subscriber_id
                logger.info(
                    "Replacing subscriber %s with %s for execution %s",
                    old_id,
                    subscriber_id,
                    execution_id,
                )
                if sq.heartbeat_task is not None and not sq.heartbeat_task.done():
                    sq.heartbeat_task.cancel()
                    sq.heartbeat_task = None

            sq.assign_subscriber(subscriber_id)

            # Start heartbeat task
            sq.heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(execution_id, subscriber_id),
                name=f"hb-{execution_id[:8]}",
            )

            queue = sq.queue

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    async with self._lock:
                        current_sq = self._queues.get(execution_id)
                        if current_sq is None or current_sq.subscriber_id != subscriber_id:
                            logger.info(
                                "Subscriber %s replaced/gone for execution %s -- ending",
                                subscriber_id,
                                execution_id,
                            )
                            return
                    logger.info(
                        "Stream timeout for execution %s (subscriber=%s) -- closing",
                        execution_id,
                        subscriber_id,
                    )
                    break

                if event is None:
                    logger.debug(
                        "Received sentinel for execution %s -- stream ending",
                        execution_id,
                    )
                    break

                yield event
                queue.task_done()

                async with self._lock:
                    s = self._queues.get(execution_id)
                    if s:
                        s.touch()

        except asyncio.CancelledError:
            logger.info(
                "Stream cancelled for execution %s (subscriber=%s)",
                execution_id,
                subscriber_id,
            )
            raise

        finally:
            async with self._lock:
                s = self._queues.get(execution_id)
                if s and s.subscriber_id == subscriber_id:
                    if s.heartbeat_task is not None and not s.heartbeat_task.done():
                        s.heartbeat_task.cancel()
                        s.heartbeat_task = None
                    s.remove_subscriber()
                    logger.debug(
                        "Subscriber %s removed from execution %s",
                        subscriber_id,
                        execution_id,
                    )

    # -- Subscriber Management ---------------------------------

    async def has_subscriber(self, execution_id: str) -> bool:
        """Check if an execution's queue has an active subscriber."""
        async with self._lock:
            sq = self._queues.get(execution_id)
            return sq is not None and sq.has_subscriber

    async def disconnect_subscriber(self, execution_id: str) -> None:
        """
        Force-disconnect the current subscriber.

        Used when cancelling an execution to ensure no stale
        subscriber holds the queue.
        """
        async with self._lock:
            sq = self._queues.get(execution_id)
            if sq is not None and sq.has_subscriber:
                if sq.heartbeat_task is not None and not sq.heartbeat_task.done():
                    sq.heartbeat_task.cancel()
                    sq.heartbeat_task = None
                sq.remove_subscriber()
                logger.info(
                    "Disconnected subscriber for execution %s", execution_id
                )

    # -- Cleanup -----------------------------------------------

    async def cleanup(self, execution_id: str) -> None:
        """
        Remove and clean up an execution's queue.

        Injects a None sentinel to wake up any subscriber stuck
        in queue.get(), ensuring clean stream termination.
        """
        sq: StreamQueue | None = None
        async with self._lock:
            sq = self._queues.pop(execution_id, None)
            if sq is None:
                return

            if sq.heartbeat_task is not None and not sq.heartbeat_task.done():
                sq.heartbeat_task.cancel()
                sq.heartbeat_task = None

        # Put sentinel WITHOUT holding lock to avoid deadlock
        if sq is not None:
            try:
                sq.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        logger.debug("Cleaned up stream for execution %s", execution_id)

    async def cleanup_all(self) -> None:
        """Remove all queues on shutdown."""
        if self._orphan_sweep_task is not None and not self._orphan_sweep_task.done():
            self._orphan_sweep_task.cancel()
            self._orphan_sweep_task = None

        async with self._lock:
            count = len(self._queues)
            for sq in self._queues.values():
                if sq.heartbeat_task is not None and not sq.heartbeat_task.done():
                    sq.heartbeat_task.cancel()
            self._queues.clear()
            logger.info("Cleaned up %d stream(s) on shutdown", count)

    # -- Orphan Sweep ------------------------------------------

    async def start_orphan_sweep(self) -> None:
        """Start the background orphan queue sweep."""
        if self._orphan_sweep_task is not None and not self._orphan_sweep_task.done():
            return
        self._orphan_sweep_task = asyncio.create_task(
            self._orphan_sweep_loop(),
            name="event-orphan-sweep",
        )
        logger.info("Orphan sweep started (interval=%ds)", ORPHAN_SWEEP_INTERVAL)

    async def _orphan_sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(ORPHAN_SWEEP_INTERVAL)
                await self._sweep_orphans()
        except asyncio.CancelledError:
            logger.debug("Orphan sweep cancelled")

    async def _sweep_orphans(self) -> int:
        """
        Clean up orphaned queues.

        A queue is orphaned if:
        - No active subscriber
        - Idle longer than IDLE_CLEANUP_TIMEOUT
        """
        orphaned: list[tuple[str, float]] = []
        async with self._lock:
            now = time.monotonic()
            for eid, sq in self._queues.items():
                if not sq.has_subscriber and (now - sq.last_activity) > IDLE_CLEANUP_TIMEOUT:
                    orphaned.append((eid, now - sq.last_activity))
                    if sq.heartbeat_task is not None and not sq.heartbeat_task.done():
                        sq.heartbeat_task.cancel()

            for eid, _ in orphaned:
                self._queues.pop(eid, None)

        for eid, idle in orphaned:
            logger.warning(
                "Swept orphaned queue: %s (idle=%.1fs)", eid, idle
            )

        if orphaned:
            logger.info("Orphan sweep cleaned %d queue(s)", len(orphaned))

        return len(orphaned)


# -- Singleton ------------------------------------------------

event_manager = EventManager()
"""Application-wide event manager instance."""
