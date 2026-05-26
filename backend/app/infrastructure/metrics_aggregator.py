"""
Metrics aggregation — Prometheus-compatible counters and histograms.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    counters: dict[str, float] = field(default_factory=dict)
    histograms: dict[str, list[float]] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MetricsAggregator:
    """In-process metrics with Prometheus text export."""

    _counters: dict[str, float] = defaultdict(float)
    _histograms: dict[str, list[float]] = defaultdict(list)
    _gauges: dict[str, float] = {}

    @classmethod
    def increment(cls, name: str, value: float = 1.0) -> None:
        cls._counters[name] += value

    @classmethod
    def observe(cls, name: str, value: float) -> None:
        cls._histograms[name].append(value)
        if len(cls._histograms[name]) > 500:
            cls._histograms[name] = cls._histograms[name][-250:]

    @classmethod
    def set_gauge(cls, name: str, value: float) -> None:
        cls._gauges[name] = value

    @classmethod
    async def aggregate(cls) -> MetricSnapshot:
        snapshot = MetricSnapshot(
            counters=dict(cls._counters),
            histograms={k: list(v) for k, v in cls._histograms.items()},
            gauges=dict(cls._gauges),
        )
        await emit_global_infrastructure_event(
            "metrics_aggregated",
            "Runtime metrics aggregated",
            counterCount=len(snapshot.counters),
            gaugeCount=len(snapshot.gauges),
        )
        return snapshot

    @classmethod
    def prometheus_text(cls) -> str:
        lines: list[str] = []
        for name, value in cls._counters.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE {safe} counter")
            lines.append(f"{safe} {value}")
        for name, value in cls._gauges.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE {safe} gauge")
            lines.append(f"{safe} {value}")
        for name, values in cls._histograms.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE {safe}_ms histogram")
            if values:
                lines.append(f"{safe}_ms_count {len(values)}")
                lines.append(f"{safe}_ms_sum {sum(values)}")
        return "\n".join(lines) + "\n"

    @classmethod
    def reset(cls) -> None:
        cls._counters.clear()
        cls._histograms.clear()
        cls._gauges.clear()
