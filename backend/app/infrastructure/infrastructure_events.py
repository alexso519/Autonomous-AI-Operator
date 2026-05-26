"""
SSE event helpers for infrastructure modules.
"""

from __future__ import annotations

import logging
from typing import Any

from app.streaming.event_manager import event_manager, StreamEvent

logger = logging.getLogger(__name__)


async def emit_infrastructure_event(
    execution_id: str,
    event_type: str,
    message: str = "",
    *,
    agent: str = "infrastructure",
    skip_persist: bool = False,
    **data: Any,
) -> None:
    """Emit an infrastructure SSE event on an execution stream."""
    try:
        event = StreamEvent(
            type=event_type,
            timestamp=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            agent=agent,
            message=message,
            data=data,
        )
        await event_manager.emit(
            execution_id,
            event,
            skip_persist=skip_persist,
        )
    except Exception as exc:
        logger.debug("Infrastructure SSE emit skipped: %s", exc)


async def emit_global_infrastructure_event(
    event_type: str,
    message: str = "",
    *,
    execution_id: str = "system",
    **data: Any,
) -> None:
    """Emit on the system stream (creates stream if needed)."""
    try:
        await event_manager.create_stream(execution_id)
    except Exception:
        pass
    await emit_infrastructure_event(
        execution_id,
        event_type,
        message,
        skip_persist=True,
        **data,
    )
