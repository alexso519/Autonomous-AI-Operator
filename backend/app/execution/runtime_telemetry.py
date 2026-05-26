"""
Runtime telemetry and diagnostics.

Tracks health metrics, retry metrics, event throughput, context pressure,
execution bottlenecks, reflection frequency, and tool orchestration latency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from app.execution.context_manager import ContextManager
from app.execution.retry_coordinator import RetryCoordinator
from app.execution.runtime_events import RuntimeEventBus

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

# Thresholds for warning emission
MAX_RETRY_RATIO = 0.6
MAX_REFLECTIONS_PER_NODE = 3
MAX_EVENT_EMIT_RATE = 200
CONTEXT_PRESSURE_THRESHOLD = 0.85


@dataclass
class RuntimeHealthSnapshot:
    execution_id: str
    duration_seconds: float = 0.0
    retry_count: int = 0
    retry_ratio: float = 0.0
    reflection_count: int = 0
    event_emit_count: int = 0
    event_dedupe_count: int = 0
    context_pressure: float = 0.0
    tool_call_count: int = 0
    bottleneck_stages: list[str] = field(default_factory=list)
    transition_count: int = 0
    contract_violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RuntimeTelemetry:
    """
    Collects runtime health metrics and emits diagnostic warnings.
    """

    _instances: dict[str, RuntimeTelemetry] = {}

    def __init__(
        self,
        execution_id: str,
        event_bus: RuntimeEventBus | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.event_bus = event_bus
        self._started_at = time.monotonic()
        self._stage_timings: dict[str, float] = {}
        self._reflection_count = 0
        self._contract_violations: list[str] = []
        RuntimeTelemetry._instances[execution_id] = self

    @classmethod
    def get(cls, execution_id: str) -> RuntimeTelemetry | None:
        return cls._instances.get(execution_id)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)

    def record_stage_start(self, stage: str) -> None:
        self._stage_timings[f"{stage}_start"] = time.monotonic()

    def record_stage_end(self, stage: str) -> None:
        start_key = f"{stage}_start"
        if start_key in self._stage_timings:
            elapsed = time.monotonic() - self._stage_timings[start_key]
            self._stage_timings[stage] = elapsed

    def record_reflection(self) -> None:
        self._reflection_count += 1

    def record_contract_violation(self, violation: str) -> None:
        self._contract_violations.append(violation)
        logger.warning(
            "Runtime contract violation [%s]: %s",
            self.execution_id,
            violation,
        )

    def build_snapshot(
        self,
        ctx: ContextManager,
        perf_snapshot: dict[str, Any] | None = None,
    ) -> RuntimeHealthSnapshot:
        duration = time.monotonic() - self._started_at
        retry_count = int(ctx.get_workflow_memory("global_retry_count") or 0)
        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        tool_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        node_count = int(ctx.get_workflow_memory("node_count") or 1)
        retry_ratio = retry_count / max(node_count, 1)

        budget_meta = ctx.get_workflow_memory("last_context_budget") or {}
        estimated_tokens = budget_meta.get("estimatedTokens", 0)
        max_tokens = budget_meta.get("maxTokens", 1) or 1
        context_pressure = min(1.0, estimated_tokens / max_tokens)

        bottlenecks = []
        if perf_snapshot:
            bottlenecks = [
                b.get("stage", "") for b in perf_snapshot.get("bottlenecks", [])
            ]

        event_stats = self.event_bus.stats if self.event_bus else {}
        transition_count = len(
            ctx.get_workflow_memory("lifecycle_transitions") or []
        )

        warnings: list[str] = []
        if retry_ratio > MAX_RETRY_RATIO:
            warnings.append(f"high_retry_ratio:{retry_ratio:.2f}")
        if self._reflection_count > node_count * MAX_REFLECTIONS_PER_NODE:
            warnings.append(f"excessive_reflections:{self._reflection_count}")
        if event_stats.get("emitCount", 0) > MAX_EVENT_EMIT_RATE:
            warnings.append(f"high_event_throughput:{event_stats.get('emitCount')}")
        if context_pressure > CONTEXT_PRESSURE_THRESHOLD:
            warnings.append(f"context_pressure:{context_pressure:.2f}")

        return RuntimeHealthSnapshot(
            execution_id=self.execution_id,
            duration_seconds=round(duration, 2),
            retry_count=retry_count,
            retry_ratio=round(retry_ratio, 3),
            reflection_count=self._reflection_count,
            event_emit_count=event_stats.get("emitCount", 0),
            event_dedupe_count=event_stats.get("dedupeCount", 0),
            context_pressure=round(context_pressure, 3),
            tool_call_count=tool_count,
            bottleneck_stages=bottlenecks,
            transition_count=transition_count,
            contract_violations=list(self._contract_violations),
            warnings=warnings,
        )

    async def emit_diagnostics(
        self,
        ctx: ContextManager,
        emit_fn: EmitFn,
        perf_snapshot: dict[str, Any] | None = None,
    ) -> RuntimeHealthSnapshot:
        """Emit runtime health warnings based on collected metrics."""
        snapshot = self.build_snapshot(ctx, perf_snapshot)

        if snapshot.warnings:
            await emit_fn(
                self.execution_id,
                "runtime_health_warning",
                "system",
                f"Runtime health: {len(snapshot.warnings)} warning(s)",
                warnings=snapshot.warnings,
                retryCount=snapshot.retry_count,
                retryRatio=snapshot.retry_ratio,
                reflectionCount=snapshot.reflection_count,
                contextPressure=snapshot.context_pressure,
                eventEmitCount=snapshot.event_emit_count,
                eventDedupeCount=snapshot.event_dedupe_count,
            )

        if snapshot.retry_ratio > MAX_RETRY_RATIO:
            await emit_fn(
                self.execution_id,
                "runtime_complexity_warning",
                "system",
                f"High retry ratio ({snapshot.retry_ratio:.0%}) — runtime complexity elevated",
                retryRatio=snapshot.retry_ratio,
                retryCount=snapshot.retry_count,
            )

        if snapshot.contract_violations:
            await emit_fn(
                self.execution_id,
                "runtime_contract_violation",
                "system",
                f"{len(snapshot.contract_violations)} contract violation(s) detected",
                violations=snapshot.contract_violations,
            )

        retry_telemetry = RetryCoordinator.get_telemetry(self.execution_id)
        ctx.set_workflow_memory(
            "runtime_telemetry_snapshot",
            {
                "durationSeconds": snapshot.duration_seconds,
                "retryCount": snapshot.retry_count,
                "retryRatio": snapshot.retry_ratio,
                "reflectionCount": snapshot.reflection_count,
                "eventEmitCount": snapshot.event_emit_count,
                "eventDedupeCount": snapshot.event_dedupe_count,
                "contextPressure": snapshot.context_pressure,
                "toolCallCount": snapshot.tool_call_count,
                "bottleneckStages": snapshot.bottleneck_stages,
                "warnings": snapshot.warnings,
                "retryTelemetryCount": len(retry_telemetry),
            },
        )

        return snapshot
