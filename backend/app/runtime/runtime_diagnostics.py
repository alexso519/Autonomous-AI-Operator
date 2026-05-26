"""
Global runtime diagnostics and scheduler health introspection.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.runtime.capability_registry import CapabilityRegistry
from app.runtime.runtime_bus import RuntimeBus


@dataclass
class ActionMetrics:
    scheduled: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    total_latency_ms: float = 0.0
    stalled_count: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.completed == 0:
            return 0.0
        return self.total_latency_ms / self.completed


@dataclass
class QueueMetrics:
    pending: int = 0
    running: int = 0
    max_depth: int = 0
    pressure_ratio: float = 0.0
    backpressure_events: int = 0


@dataclass
class CapabilityMetrics:
    selections: int = 0
    failures: int = 0
    unavailable: int = 0
    degraded_mode_activations: int = 0


@dataclass
class SchedulerHealth:
    status: str = "healthy"
    imbalance_score: float = 0.0
    starvation_prevented: int = 0
    retry_storm_detected: bool = False
    degraded_mode: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "imbalanceScore": self.imbalance_score,
            "starvationPrevented": self.starvation_prevented,
            "retryStormDetected": self.retry_storm_detected,
            "degradedMode": self.degraded_mode,
        }


class RuntimeDiagnostics:
    """
    Global runtime introspection for operator visibility.
    """

    _instances: dict[str, RuntimeDiagnostics] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.action_metrics = ActionMetrics()
        self.queue_metrics = QueueMetrics()
        self.capability_metrics = CapabilityMetrics()
        self.scheduler_health = SchedulerHealth()
        self._orchestration_bottlenecks: list[str] = []
        self._memory_pressure: float = 0.0
        self._started_at = time.monotonic()

    @classmethod
    def for_execution(cls, execution_id: str) -> RuntimeDiagnostics:
        if execution_id not in cls._instances:
            cls._instances[execution_id] = cls(execution_id)
        return cls._instances[execution_id]

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)

    def record_action_scheduled(self) -> None:
        self.action_metrics.scheduled += 1
        self.queue_metrics.pending += 1
        self.queue_metrics.max_depth = max(
            self.queue_metrics.max_depth, self.queue_metrics.pending
        )

    def record_action_started(self) -> None:
        self.queue_metrics.pending = max(0, self.queue_metrics.pending - 1)
        self.queue_metrics.running += 1

    def record_action_completed(self, latency_ms: float) -> None:
        self.queue_metrics.running = max(0, self.queue_metrics.running - 1)
        self.action_metrics.completed += 1
        self.action_metrics.total_latency_ms += latency_ms

    def record_action_failed(self) -> None:
        self.queue_metrics.running = max(0, self.queue_metrics.running - 1)
        self.action_metrics.failed += 1

    def record_action_cancelled(self) -> None:
        self.queue_metrics.running = max(0, self.queue_metrics.running - 1)
        self.action_metrics.cancelled += 1

    def record_capability_failure(self) -> None:
        self.capability_metrics.failures += 1

    def record_backpressure(self) -> None:
        self.queue_metrics.backpressure_events += 1
        self._update_pressure()

    def record_retry_burst(self, count: int) -> None:
        if count >= 5:
            self.scheduler_health.retry_storm_detected = True
            self.scheduler_health.status = "degraded"

    def record_degraded_mode(self) -> None:
        self.capability_metrics.degraded_mode_activations += 1
        self.scheduler_health.degraded_mode = True
        self.scheduler_health.status = "degraded"

    def record_bottleneck(self, name: str) -> None:
        if name not in self._orchestration_bottlenecks:
            self._orchestration_bottlenecks.append(name)

    def set_memory_pressure(self, ratio: float) -> None:
        self._memory_pressure = min(1.0, max(0.0, ratio))

    def _update_pressure(self) -> None:
        cap = 64
        depth = self.queue_metrics.pending + self.queue_metrics.running
        self.queue_metrics.pressure_ratio = min(1.0, depth / cap)
        if self.queue_metrics.pressure_ratio > 0.8:
            self.scheduler_health.status = "pressure"

    def snapshot(
        self,
        *,
        bus: RuntimeBus | None = None,
        registry: CapabilityRegistry | None = None,
    ) -> dict[str, Any]:
        uptime = time.monotonic() - self._started_at
        return {
            "executionId": self.execution_id,
            "uptimeSeconds": round(uptime, 2),
            "actions": {
                "scheduled": self.action_metrics.scheduled,
                "completed": self.action_metrics.completed,
                "failed": self.action_metrics.failed,
                "cancelled": self.action_metrics.cancelled,
                "avgLatencyMs": round(self.action_metrics.avg_latency_ms, 2),
                "stalled": self.action_metrics.stalled_count,
            },
            "queue": {
                "pending": self.queue_metrics.pending,
                "running": self.queue_metrics.running,
                "maxDepth": self.queue_metrics.max_depth,
                "pressureRatio": round(self.queue_metrics.pressure_ratio, 3),
                "backpressureEvents": self.queue_metrics.backpressure_events,
            },
            "capabilities": {
                "selections": self.capability_metrics.selections,
                "failures": self.capability_metrics.failures,
                "unavailable": self.capability_metrics.unavailable,
                "degradedActivations": self.capability_metrics.degraded_mode_activations,
                "registry": registry.stats() if registry else {},
            },
            "scheduler": self.scheduler_health.to_dict(),
            "memoryPressure": round(self._memory_pressure, 3),
            "bottlenecks": list(self._orchestration_bottlenecks),
            "bus": bus.stats if bus else {},
        }
