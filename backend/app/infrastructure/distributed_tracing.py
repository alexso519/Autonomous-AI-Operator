"""
OpenTelemetry-compatible distributed tracing (optional).
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    name: str
    execution_id: str = ""
    parent_span_id: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        duration_ms = (
            (self.end_time - self.start_time) * 1000 if self.end_time else None
        )
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "executionId": self.execution_id,
            "durationMs": duration_ms,
            "attributes": self.attributes,
            "status": self.status,
        }


class DistributedTracing:
    """Lightweight tracing with optional OTEL export."""

    _spans: dict[str, TraceSpan] = {}
    _otel_tracer: Any = None

    @classmethod
    def _ensure_otel(cls) -> None:
        if cls._otel_tracer or not settings.enable_otel_tracing:
            return
        try:
            from opentelemetry import trace

            cls._otel_tracer = trace.get_tracer("autonomous-ai-operator")
        except ImportError:
            logger.info("OpenTelemetry not installed — using in-process tracing")

    @classmethod
    def start_span(
        cls,
        name: str,
        *,
        execution_id: str = "",
        parent_span_id: str = "",
        **attributes: Any,
    ) -> TraceSpan:
        cls._ensure_otel()
        span = TraceSpan(
            trace_id=uuid.uuid4().hex[:32],
            span_id=uuid.uuid4().hex[:16],
            name=name,
            execution_id=execution_id,
            parent_span_id=parent_span_id,
            attributes=attributes,
        )
        cls._spans[span.span_id] = span
        return span

    @classmethod
    async def end_span(cls, span: TraceSpan, *, status: str = "ok") -> None:
        span.end_time = time.time()
        span.status = status
        if span.execution_id:
            await emit_infrastructure_event(
                span.execution_id,
                "trace_completed",
                f"Span {span.name} completed",
                traceId=span.trace_id,
                spanId=span.span_id,
                durationMs=(span.end_time - span.start_time) * 1000,
                status=status,
            )

    @classmethod
    @asynccontextmanager
    async def trace_action(
        cls,
        name: str,
        *,
        execution_id: str = "",
        **attributes: Any,
    ) -> AsyncIterator[TraceSpan]:
        span = cls.start_span(name, execution_id=execution_id, **attributes)
        if execution_id:
            await emit_infrastructure_event(
                execution_id,
                "trace_started",
                f"Trace started: {name}",
                traceId=span.trace_id,
                spanId=span.span_id,
            )
        try:
            yield span
            await cls.end_span(span, status="ok")
        except Exception:
            await cls.end_span(span, status="error")
            raise

    @classmethod
    def get_recent_spans(cls, *, limit: int = 50) -> list[dict[str, Any]]:
        spans = sorted(
            cls._spans.values(),
            key=lambda s: s.start_time,
            reverse=True,
        )[:limit]
        return [s.to_dict() for s in spans]

    @classmethod
    def export_hooks(cls) -> dict[str, Any]:
        return {
            "otelEnabled": settings.enable_otel_tracing,
            "otelAvailable": cls._otel_tracer is not None,
            "spanCount": len(cls._spans),
        }
