"""
Memory telemetry and observability for unified context architecture.

Emits structured events for retrieval, ranking, assembly, and token pressure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class RetrievalTrace:
    """Inspectable trace of a single retrieval operation."""

    trace_id: str
    strategy: str
    namespaces_requested: list[str]
    records_retrieved: int
    records_after_dedup: int
    records_after_rank: int
    conflicts_detected: int = 0
    duration_ms: float = 0.0
    ranking_reasons: list[dict[str, Any]] = field(default_factory=list)
    section_contributions: dict[str, int] = field(default_factory=dict)


@dataclass
class ContextAssemblyReport:
    """Post-assembly diagnostics."""

    total_chars: int
    estimated_tokens: int
    sections: list[dict[str, Any]]
    truncated_sections: list[str]
    budget_utilization: float
    memory_pressure: float
    sources: list[str]


class MemoryTelemetry:
    """Collects and emits memory-layer observability events."""

    _instances: dict[str, MemoryTelemetry] = {}

    def __init__(self, execution_id: str = "") -> None:
        self.execution_id = execution_id
        self._traces: list[RetrievalTrace] = []
        self._events: list[dict[str, Any]] = []
        if execution_id:
            MemoryTelemetry._instances[execution_id] = self

    @classmethod
    def get(cls, execution_id: str) -> MemoryTelemetry | None:
        return cls._instances.get(execution_id)

    @classmethod
    def for_execution(cls, execution_id: str) -> MemoryTelemetry:
        existing = cls._instances.get(execution_id)
        if existing:
            return existing
        return cls(execution_id)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        entry = {
            "type": event_type,
            "executionId": self.execution_id,
            "timestamp": time.time(),
            **payload,
        }
        self._events.append(entry)
        logger.debug("memory_telemetry.%s: %s", event_type, payload)

    async def emit(
        self,
        emit_fn: EmitFn | None,
        event_type: str,
        message: str,
        **data: Any,
    ) -> None:
        self._record(event_type, {"message": message, **data})
        if emit_fn and self.execution_id:
            try:
                await emit_fn(
                    self.execution_id,
                    event_type,
                    "memory",
                    message,
                    **data,
                )
            except Exception as exc:
                logger.debug("Memory telemetry emit failed: %s", exc)

    async def memory_retrieved(
        self,
        trace: RetrievalTrace,
        emit_fn: EmitFn | None = None,
    ) -> None:
        self._traces.append(trace)
        await self.emit(
            emit_fn,
            "memory_retrieved",
            f"Retrieved {trace.records_after_rank} memory records",
            traceId=trace.trace_id,
            strategy=trace.strategy,
            namespaces=trace.namespaces_requested,
            retrieved=trace.records_retrieved,
            afterDedup=trace.records_after_dedup,
            afterRank=trace.records_after_rank,
            durationMs=round(trace.duration_ms, 2),
        )

    async def memory_ranked(
        self,
        trace_id: str,
        ranked_count: int,
        top_reasons: list[dict[str, Any]],
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "memory_ranked",
            f"Ranked {ranked_count} memory records",
            traceId=trace_id,
            rankedCount=ranked_count,
            topReasons=top_reasons[:5],
        )

    async def context_assembled(
        self,
        report: ContextAssemblyReport,
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "context_assembled",
            f"Context assembled: ~{report.estimated_tokens} tokens",
            totalChars=report.total_chars,
            estimatedTokens=report.estimated_tokens,
            sections=report.sections,
            budgetUtilization=round(report.budget_utilization, 3),
            sources=report.sources,
        )

    async def context_truncated(
        self,
        sections: list[str],
        chars_removed: int,
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "context_truncated",
            f"Truncated {len(sections)} section(s), ~{chars_removed} chars removed",
            truncatedSections=sections,
            charsRemoved=chars_removed,
        )

    async def retrieval_conflict_detected(
        self,
        conflict_type: str,
        sources: list[str],
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "retrieval_conflict_detected",
            f"Retrieval conflict: {conflict_type}",
            conflictType=conflict_type,
            sources=sources,
        )

    async def retrieval_strategy_selected(
        self,
        strategy: str,
        namespaces: list[str],
        reason: str,
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "retrieval_strategy_selected",
            f"Strategy '{strategy}' selected",
            strategy=strategy,
            namespaces=namespaces,
            reason=reason,
        )

    async def memory_pressure_warning(
        self,
        utilization: float,
        threshold: float,
        emit_fn: EmitFn | None = None,
    ) -> None:
        await self.emit(
            emit_fn,
            "memory_pressure_warning",
            f"Memory pressure {utilization:.0%} exceeds {threshold:.0%}",
            utilization=round(utilization, 3),
            threshold=threshold,
        )

    def get_traces(self) -> list[RetrievalTrace]:
        return list(self._traces)

    def get_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def get_debug_snapshot(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "traceCount": len(self._traces),
            "eventCount": len(self._events),
            "recentTraces": [
                {
                    "traceId": t.trace_id,
                    "strategy": t.strategy,
                    "retrieved": t.records_retrieved,
                    "afterRank": t.records_after_rank,
                    "durationMs": t.duration_ms,
                }
                for t in self._traces[-5:]
            ],
        }
