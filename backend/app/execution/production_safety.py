"""
Production safety — rate limiting, circuit breakers, duration caps, degraded mode.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheckResult:
    allowed: bool
    reason: str = ""
    degraded: bool = False


class ProductionSafetyGuard:
    """Single-node safety controls for autonomous runtime."""

    _start_timestamps: dict[str, float] = {}
    _retry_counts: dict[str, int] = {}
    _execution_starts: deque[float] = deque(maxlen=200)
    _global_retry_spiral: dict[str, int] = {}

    MAX_CONCURRENT_EXECUTIONS = 3
    RATE_LIMIT_WINDOW_SECONDS = 60
    RATE_LIMIT_MAX_STARTS = 5
    MAX_EXECUTION_DURATION_SECONDS = 3600
    MAX_RETRY_SPIRAL = 8
    MEMORY_PRESSURE_THRESHOLD_MB = 512

    @classmethod
    def record_execution_start(cls, execution_id: str) -> None:
        now = time.time()
        cls._start_timestamps[execution_id] = now
        cls._execution_starts.append(now)
        cls._retry_counts[execution_id] = 0

    @classmethod
    def record_retry(cls, execution_id: str) -> int:
        count = cls._retry_counts.get(execution_id, 0) + 1
        cls._retry_counts[execution_id] = count
        spiral = cls._global_retry_spiral.get(execution_id, 0) + 1
        cls._global_retry_spiral[execution_id] = spiral
        return count

    @classmethod
    def clear_execution(cls, execution_id: str) -> None:
        cls._start_timestamps.pop(execution_id, None)
        cls._retry_counts.pop(execution_id, None)
        cls._global_retry_spiral.pop(execution_id, None)

    @classmethod
    def check_rate_limit(cls, active_count: int) -> SafetyCheckResult:
        if active_count >= cls.MAX_CONCURRENT_EXECUTIONS:
            return SafetyCheckResult(
                allowed=False,
                reason="max_concurrent_executions_reached",
            )

        now = time.time()
        recent = [t for t in cls._execution_starts if now - t < cls.RATE_LIMIT_WINDOW_SECONDS]
        if len(recent) >= cls.RATE_LIMIT_MAX_STARTS:
            return SafetyCheckResult(
                allowed=False,
                reason="execution_rate_limit_exceeded",
            )
        return SafetyCheckResult(allowed=True)

    @classmethod
    def check_retry_circuit(cls, execution_id: str) -> SafetyCheckResult:
        spiral = cls._global_retry_spiral.get(execution_id, 0)
        if spiral >= cls.MAX_RETRY_SPIRAL:
            return SafetyCheckResult(
                allowed=False,
                reason="retry_circuit_breaker_open",
            )
        if spiral >= cls.MAX_RETRY_SPIRAL - 2:
            return SafetyCheckResult(
                allowed=True,
                degraded=True,
                reason="retry_budget_low_degraded_mode",
            )
        return SafetyCheckResult(allowed=True)

    @classmethod
    def check_duration(cls, execution_id: str) -> SafetyCheckResult:
        start = cls._start_timestamps.get(execution_id)
        if not start:
            return SafetyCheckResult(allowed=True)
        elapsed = time.time() - start
        max_dur = settings.max_execution_duration_seconds
        if elapsed >= max_dur:
            return SafetyCheckResult(
                allowed=False,
                reason=f"max_execution_duration_exceeded_{int(elapsed)}s",
            )
        if elapsed >= max_dur * 0.85:
            return SafetyCheckResult(
                allowed=True,
                degraded=True,
                reason="approaching_max_duration",
            )
        return SafetyCheckResult(allowed=True)

    @classmethod
    def check_memory_pressure(cls) -> SafetyCheckResult:
        try:
            import resource

            usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux: KB, macOS: bytes — normalize roughly
            mb = usage_kb / 1024 if usage_kb < 10_000_000 else usage_kb / (1024 * 1024)
            if mb > cls.MEMORY_PRESSURE_THRESHOLD_MB:
                return SafetyCheckResult(
                    allowed=True,
                    degraded=True,
                    reason="memory_pressure_degraded_mode",
                )
        except Exception:
            pass
        return SafetyCheckResult(allowed=True)

    @classmethod
    def pre_start_check(cls, active_count: int) -> SafetyCheckResult:
        rate = cls.check_rate_limit(active_count)
        if not rate.allowed:
            return rate
        mem = cls.check_memory_pressure()
        if mem.degraded:
            logger.warning("Starting in degraded mode: %s", mem.reason)
        return SafetyCheckResult(allowed=True, degraded=mem.degraded)

    @classmethod
    def mid_execution_check(cls, execution_id: str) -> SafetyCheckResult:
        duration = cls.check_duration(execution_id)
        if not duration.allowed:
            return duration
        retry = cls.check_retry_circuit(execution_id)
        if not retry.allowed:
            return retry
        mem = cls.check_memory_pressure()
        return SafetyCheckResult(
            allowed=True,
            degraded=duration.degraded or retry.degraded or mem.degraded,
            reason=duration.reason or retry.reason or mem.reason,
        )

    @classmethod
    def isolate_tool_failure(cls, tool_name: str, error: str) -> dict[str, Any]:
        """Return isolated failure payload — tool failure must not crash runtime."""
        return {
            "isolated": True,
            "tool": tool_name,
            "error": error[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "continueExecution": True,
        }
