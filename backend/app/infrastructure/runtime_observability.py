"""
Runtime observability — health summaries and diagnostic hooks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


@dataclass
class RuntimeHealthSummary:
    status: str = "healthy"
    active_executions: int = 0
    queue_depth: int = 0
    worker_count: int = 0
    degraded: bool = False
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "activeExecutions": self.active_executions,
            "queueDepth": self.queue_depth,
            "workerCount": self.worker_count,
            "degraded": self.degraded,
            "latencyP50Ms": self.latency_p50_ms,
            "latencyP99Ms": self.latency_p99_ms,
            "collectedAt": self.collected_at,
        }


class RuntimeObservability:
    """Central observability facade."""

    _latencies: list[float] = []

    @classmethod
    def record_latency(cls, duration_ms: float) -> None:
        cls._latencies.append(duration_ms)
        if len(cls._latencies) > 1000:
            cls._latencies = cls._latencies[-500:]

    @classmethod
    async def collect_health_summary(cls) -> RuntimeHealthSummary:
        from app.execution.manager import execution_manager
        from app.infrastructure.distributed_queue import DistributedQueue
        from app.infrastructure.worker_runtime import WorkerRuntime

        depth = await DistributedQueue.get_instance().queue_depth()
        workers = await WorkerRuntime.get_instance().list_workers()
        latencies = sorted(cls._latencies)
        p50 = latencies[len(latencies) // 2] if latencies else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

        summary = RuntimeHealthSummary(
            active_executions=execution_manager.count_active(),
            queue_depth=depth.get("pending", 0),
            worker_count=len(workers),
            latency_p50_ms=p50,
            latency_p99_ms=p99,
        )
        if summary.queue_depth > 50 or summary.active_executions > 20:
            summary.status = "degraded"
            summary.degraded = True

        await emit_global_infrastructure_event(
            "runtime_health_updated",
            f"Runtime health: {summary.status}",
            **summary.to_dict(),
        )
        return summary

    @classmethod
    async def get_diagnostics(cls) -> dict[str, Any]:
        summary = await cls.collect_health_summary()
        return {
            "health": summary.to_dict(),
            "otelEnabled": settings.enable_otel_tracing,
            "distributedEnabled": settings.enable_distributed_runtime,
        }
