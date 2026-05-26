"""
Action scheduler — adaptive action sequencing for desktop workflows.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScheduledAction:
    action_type: str
    target: str
    step_index: int
    priority: int = 5
    scheduled_at: str = field(default_factory=_now)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionType": self.action_type,
            "target": self.target,
            "stepIndex": self.step_index,
            "priority": self.priority,
            "scheduledAt": self.scheduled_at,
            "status": self.status,
        }


class ActionScheduler:
    """Schedule and pace desktop actions with rate limiting."""

    _queues: dict[str, list[ScheduledAction]] = {}
    _last_action_time: dict[str, float] = {}

    def __init__(self, execution_id: str, *, min_interval_ms: int = 100) -> None:
        self.execution_id = execution_id
        self.min_interval_ms = min_interval_ms

    async def schedule(
        self,
        action_type: str,
        target: str,
        *,
        step_index: int = 0,
        priority: int = 5,
    ) -> ScheduledAction:
        action = ScheduledAction(
            action_type=action_type,
            target=target,
            step_index=step_index,
            priority=priority,
        )
        self._queues.setdefault(self.execution_id, []).append(action)
        await self._pace()
        action.status = "executing"
        return action

    async def schedule_background(
        self,
        actions: list[dict[str, Any]],
    ) -> None:
        """Queue actions for background continuation."""
        queue = self._queues.setdefault(self.execution_id, [])
        for i, a in enumerate(actions):
            queue.append(ScheduledAction(
                action_type=a.get("action", "observe"),
                target=a.get("target", ""),
                step_index=a.get("stepIndex", i),
                priority=a.get("priority", 3),
            ))

    def pending_count(self) -> int:
        queue = self._queues.get(self.execution_id, [])
        return sum(1 for a in queue if a.status == "pending")

    def to_dict(self) -> dict[str, Any]:
        queue = self._queues.get(self.execution_id, [])
        return {
            "executionId": self.execution_id,
            "pending": self.pending_count(),
            "queue": [a.to_dict() for a in queue[-20:]],
        }

    async def _pace(self) -> None:
        import time

        now = time.monotonic()
        last = self._last_action_time.get(self.execution_id, 0)
        elapsed_ms = (now - last) * 1000
        if elapsed_ms < self.min_interval_ms:
            await asyncio.sleep((self.min_interval_ms - elapsed_ms) / 1000)
        self._last_action_time[self.execution_id] = time.monotonic()

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._queues.pop(execution_id, None)
        cls._last_action_time.pop(execution_id, None)
