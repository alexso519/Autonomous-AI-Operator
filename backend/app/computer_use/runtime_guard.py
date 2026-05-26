"""
Runtime guard — deadlock detection, runaway suppression, emergency stop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GuardState:
    action_count: int = 0
    last_action_time: float = 0.0
    emergency_stop: bool = False
    deadlock_detected: bool = False
    idle_since: float = field(default_factory=time.monotonic)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionCount": self.action_count,
            "emergencyStop": self.emergency_stop,
            "deadlockDetected": self.deadlock_detected,
            "warningCount": len(self.warnings),
            "warnings": self.warnings[-5:],
        }


class RuntimeGuard:
    """Desktop runtime safety guard with deadlock and runaway detection."""

    _states: dict[str, GuardState] = {}
    MAX_ACTIONS_PER_MINUTE = 60
    DEADLOCK_IDLE_SECONDS = 120
    MAX_ACTIONS_SESSION = 500

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        if execution_id not in self._states:
            self._states[execution_id] = GuardState()

    @property
    def state(self) -> GuardState:
        return self._states[self.execution_id]

    def record_action(self) -> bool:
        """Record an action; return False if blocked."""
        s = self.state
        if s.emergency_stop:
            return False

        now = time.monotonic()
        s.action_count += 1
        s.last_action_time = now
        s.idle_since = now

        if s.action_count > self.MAX_ACTIONS_SESSION:
            s.emergency_stop = True
            s.warnings.append("max_session_actions_exceeded")
            logger.warning("Emergency stop: max actions for %s", self.execution_id[:8])
            return False

        return True

    def check_runaway(self) -> bool:
        """Return True if runaway action rate detected."""
        s = self.state
        if s.action_count < 10:
            return False
        elapsed = time.monotonic() - s.idle_since
        if elapsed < 60:
            rate = s.action_count / max(elapsed, 1) * 60
            if rate > self.MAX_ACTIONS_PER_MINUTE:
                s.warnings.append(f"runaway_rate:{rate:.0f}/min")
                s.emergency_stop = True
                return True
        return False

    def check_deadlock(self) -> bool:
        s = self.state
        idle = time.monotonic() - s.idle_since
        if idle > self.DEADLOCK_IDLE_SECONDS and s.action_count > 0:
            s.deadlock_detected = True
            s.warnings.append(f"deadlock_idle:{idle:.0f}s")
            return True
        return False

    def emergency_stop(self, reason: str = "") -> None:
        s = self.state
        s.emergency_stop = True
        s.warnings.append(f"emergency_stop:{reason}")
        logger.warning("Emergency stop for %s: %s", self.execution_id[:8], reason)

    def is_allowed(self) -> bool:
        s = self.state
        if s.emergency_stop:
            return False
        if self.check_deadlock():
            return False
        if self.check_runaway():
            return False
        return True

    def reset_idle_timer(self) -> None:
        self.state.idle_since = time.monotonic()

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._states.pop(execution_id, None)
