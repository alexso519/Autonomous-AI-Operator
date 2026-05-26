"""
Structured JSON logging pipeline for execution diagnostics.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class LogPipeline:
    """Structured log emission for replay diagnostics."""

    @staticmethod
    def emit(
        level: str,
        message: str,
        *,
        execution_id: str = "",
        component: str = "infrastructure",
        **fields: Any,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "message": message,
            "executionId": execution_id,
            "component": component,
            **fields,
        }
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn(json.dumps(record, default=str))

    @staticmethod
    def execution_replay_log(
        execution_id: str,
        event_type: str,
        *,
        message: str = "",
        **data: Any,
    ) -> None:
        LogPipeline.emit(
            "info",
            message or f"Replay diagnostic: {event_type}",
            execution_id=execution_id,
            eventType=event_type,
            replayDiagnostic=True,
            **data,
        )

    @staticmethod
    def sse_diagnostic(
        execution_id: str,
        event_type: str,
        *,
        queue_depth: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        LogPipeline.emit(
            "debug",
            f"SSE stream diagnostic: {event_type}",
            execution_id=execution_id,
            eventType=event_type,
            queueDepth=queue_depth,
            latencyMs=latency_ms,
            sseDiagnostic=True,
        )
