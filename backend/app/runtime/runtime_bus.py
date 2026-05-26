"""
Unified runtime bus — canonical internal communication layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

HandlerFn = Callable[["RuntimeMessage"], Awaitable[None]]


class RuntimeChannel(str, Enum):
    EXECUTION = "execution"
    COGNITION = "cognition"
    TOOLS = "tools"
    BROWSER = "browser"
    CODING = "coding"
    MEMORY = "memory"
    TELEMETRY = "telemetry"
    RETRY = "retry"
    REFLECTION = "reflection"
    PLANNING = "planning"


@dataclass
class RuntimeMessage:
    """Message published on the runtime bus."""

    channel: RuntimeChannel
    event_type: str
    execution_id: str
    agent: str = "system"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def dedupe_key(self) -> str:
        raw = f"{self.channel.value}|{self.event_type}|{self.agent}|{self.message[:200]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_sse_payload(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "message": self.message,
            "data": {
                **self.data,
                "channel": self.channel.value,
                "correlationId": self.correlation_id,
                "messageId": self.message_id,
            },
        }


@dataclass
class EventSubscription:
    """Subscription to a runtime channel."""

    channel: RuntimeChannel
    handler: HandlerFn
    subscription_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class RuntimeBus:
    """
    Async publish/subscribe bus with replay, deduplication, and backpressure.
    """

    DEFAULT_QUEUE_SIZE = 512
    DEDUPE_WINDOW = 64

    _instances: dict[str, RuntimeBus] = {}

    def __init__(self, execution_id: str, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self.execution_id = execution_id
        self.queue_size = queue_size
        self._subscriptions: dict[RuntimeChannel, list[EventSubscription]] = defaultdict(list)
        self._global_handlers: list[EventSubscription] = []
        self._replay_buffer: deque[RuntimeMessage] = deque(maxlen=1024)
        self._dedupe_keys: deque[str] = deque(maxlen=self.DEDUPE_WINDOW)
        self._dead_letter: deque[RuntimeMessage] = deque(maxlen=64)
        self._publish_count = 0
        self._dropped_count = 0
        self._lock = asyncio.Lock()
        self._sse_bridge: Callable[[RuntimeMessage], Awaitable[None]] | None = None
        self._persist_hook: Callable[[RuntimeMessage], Awaitable[None]] | None = None

    @classmethod
    def for_execution(cls, execution_id: str) -> RuntimeBus:
        if execution_id not in cls._instances:
            cls._instances[execution_id] = cls(execution_id)
        return cls._instances[execution_id]

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)

    def set_sse_bridge(self, bridge: Callable[[RuntimeMessage], Awaitable[None]]) -> None:
        self._sse_bridge = bridge

    def set_persist_hook(self, hook: Callable[[RuntimeMessage], Awaitable[None]]) -> None:
        self._persist_hook = hook

    def subscribe(
        self,
        channel: RuntimeChannel,
        handler: HandlerFn,
    ) -> EventSubscription:
        sub = EventSubscription(channel=channel, handler=handler)
        self._subscriptions[channel].append(sub)
        return sub

    def subscribe_all(self, handler: HandlerFn) -> EventSubscription:
        sub = EventSubscription(channel=RuntimeChannel.TELEMETRY, handler=handler)
        self._global_handlers.append(sub)
        return sub

    def unsubscribe(self, subscription_id: str) -> None:
        for channel, subs in self._subscriptions.items():
            self._subscriptions[channel] = [
                s for s in subs if s.subscription_id != subscription_id
            ]
        self._global_handlers = [
            s for s in self._global_handlers if s.subscription_id != subscription_id
        ]

    async def publish(
        self,
        channel: RuntimeChannel,
        event_type: str,
        message: str = "",
        *,
        agent: str = "system",
        skip_dedupe: bool = False,
        **data: Any,
    ) -> bool:
        msg = RuntimeMessage(
            channel=channel,
            event_type=event_type,
            execution_id=self.execution_id,
            agent=agent,
            message=message,
            data=data,
            correlation_id=data.pop("correlationId", "") or uuid.uuid4().hex[:16],
        )

        if not skip_dedupe:
            key = msg.dedupe_key()
            if key in self._dedupe_keys:
                return False
            self._dedupe_keys.append(key)

        notify_backpressure = False
        async with self._lock:
            if len(self._replay_buffer) >= self.queue_size:
                self._dropped_count += 1
                self._dead_letter.append(msg)
                notify_backpressure = True
            else:
                self._replay_buffer.append(msg)
                self._publish_count += 1

        if notify_backpressure:
            await self._notify_backpressure()
            return False

        await self._dispatch(msg)
        return True

    async def _dispatch(self, msg: RuntimeMessage) -> None:
        handlers = list(self._subscriptions.get(msg.channel, []))
        handlers.extend(self._global_handlers)

        for sub in handlers:
            try:
                await sub.handler(msg)
            except Exception:
                logger.exception(
                    "Bus handler failed on %s/%s", msg.channel.value, msg.event_type
                )
                self._dead_letter.append(msg)

        if self._sse_bridge:
            try:
                await self._sse_bridge(msg)
            except Exception:
                logger.exception("SSE bridge failed for %s", msg.event_type)

        if self._persist_hook:
            try:
                await self._persist_hook(msg)
            except Exception:
                logger.exception("Persist hook failed for %s", msg.event_type)

    async def _notify_backpressure(self) -> None:
        msg = RuntimeMessage(
            channel=RuntimeChannel.TELEMETRY,
            event_type="runtime_backpressure",
            execution_id=self.execution_id,
            message="Runtime bus queue at capacity — applying backpressure",
            data={
                "queueSize": self.queue_size,
                "droppedCount": self._dropped_count,
            },
        )
        await self._dispatch(msg)

    def replay(self, since_index: int = 0) -> list[RuntimeMessage]:
        return list(self._replay_buffer)[since_index:]

    def replay_as_dicts(self) -> list[dict[str, Any]]:
        return [m.to_sse_payload() for m in self._replay_buffer]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "publishCount": self._publish_count,
            "droppedCount": self._dropped_count,
            "bufferSize": len(self._replay_buffer),
            "deadLetterSize": len(self._dead_letter),
            "subscriptionCount": sum(len(v) for v in self._subscriptions.values())
            + len(self._global_handlers),
        }
