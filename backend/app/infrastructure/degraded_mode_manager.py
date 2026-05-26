"""
Degraded mode manager — reduced-capability execution under pressure.
"""

from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.infrastructure_events import emit_global_infrastructure_event
from app.infrastructure.metrics_aggregator import MetricsAggregator

logger = logging.getLogger(__name__)


class DegradedModeManager:
    """Tracks and enforces degraded execution mode."""

    _degraded: bool = False
    _reason: str = ""

    @classmethod
    async def enable(cls, reason: str) -> dict[str, Any]:
        if not cls._degraded:
            cls._degraded = True
            cls._reason = reason
            await emit_global_infrastructure_event(
                "degraded_mode_enabled",
                f"Degraded mode enabled: {reason}",
                reason=reason,
            )
            MetricsAggregator.increment("runtime.degraded.enabled")
        return cls.status()

    @classmethod
    async def disable(cls) -> dict[str, Any]:
        cls._degraded = False
        cls._reason = ""
        return cls.status()

    @classmethod
    def is_degraded(cls) -> bool:
        return cls._degraded

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "degraded": cls._degraded,
            "reason": cls._reason,
            "restrictions": cls._restrictions() if cls._degraded else [],
        }

    @classmethod
    def _restrictions(cls) -> list[str]:
        return [
            "parallelism_reduced",
            "non_critical_features_disabled",
            "extended_retry_backoff",
        ]

    @classmethod
    async def evaluate_pressure(cls, queue_depth: int, active_executions: int) -> bool:
        if queue_depth > 100 or active_executions > 30:
            await cls.enable(
                f"queue_depth={queue_depth}, active={active_executions}"
            )
            return True
        if cls._degraded and queue_depth < 20 and active_executions < 10:
            await cls.disable()
        return cls._degraded
