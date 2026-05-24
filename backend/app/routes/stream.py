"""
SSE streaming endpoint for live execution events.

Flow:
  1. Client calls POST /api/workflows/{id}/run → returns execution_id immediately
  2. Client opens GET /api/executions/{execution_id}/stream (EventSource in browser)
  3. Backend streams events as the execution progresses
  4. Stream ends naturally when execution completes, fails, or pauses for approval

Reconnect flow:
  1. On disconnect, frontend reconnects with ?since={last_timestamp}
  2. Backend replays historical events from execution_event_logs
  3. Then transitions to live queue subscription
  4. Heartbeat events keep the connection alive during idle periods

Event types:
  workflow_started     → execution began
  agent_started        → an agent is now active
  thinking             → agent is thinking (sub-actions)
  log                  → generic log line
  output               → agent produced output
  agent_completed      → agent finished its step
  approval_requested   → execution paused, waiting for human approval
  approval_granted     → human approved, execution resuming
  approval_rejected    → human rejected, execution cancelled
  execution_resumed    → execution continuing after approval
  workflow_completed   → all agents finished successfully
  workflow_failed      → execution aborted with error
  stream_end           → stream closed normally
  stream_error         → stream-level error
  stream_heartbeat     → keepalive (every 10s during inactivity, resets timeout)

Format:
  event: <type>
  data: {"type":"<type>","timestamp":"...","agent":"...","message":"...","data":{}}

Clients use EventSource API or fetch with ReadableStream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.streaming.event_manager import event_manager, StreamEvent, SUBSCRIBE_TIMEOUT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/executions", tags=["executions"])


# ── SSE Streaming ─────────────────────────────────────────────────


@router.get("/{execution_id}/stream")
async def stream_execution(
    execution_id: str,
    request: Request,
    since: str | None = Query(
        default=None,
        description="ISO timestamp — replay events after this point before going live",
    ),
):
    """
    SSE endpoint that streams execution events in real time.

    The client opens this as an EventSource after starting an execution.
    Events are streamed as `text/event-stream` until the execution
    completes, fails, or the client disconnects.

    Reconnect with ?since=ISO_TIMESTAMP to replay missed events:
      fetch(`/api/executions/${executionId}/stream?since=${lastTimestamp}`)

    Heartbeat events (stream_heartbeat) are emitted every 10s during
    idle periods to keep the connection alive. The frontend should
    treat heartbeats as no-ops.

    Usage (frontend):
      const es = new EventSource(`/api/executions/${executionId}/stream`);
      es.addEventListener('workflow_completed', (e) => { ... });
    """
    # Verify execution exists in database
    from app.database.database import get_db

    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status FROM executions WHERE id = ?", (execution_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    execution_status = dict(row)["status"]

    # If execution is waiting for approval, send the status and return
    if execution_status == "waiting_approval":
        async def waiting_generator():
            yield _format_sse(
                "approval_requested",
                StreamEvent(
                    type="approval_requested",
                    agent="system",
                    message="Execution is paused — waiting for human approval",
                    data={"executionId": execution_id, "paused": True},
                ),
            )
            # Keep the connection alive briefly so the client gets the event
            yield _format_sse(
                "stream_end",
                StreamEvent(
                    type="stream_end",
                    agent="system",
                    message="Stream parked — reconnect after approval",
                ),
            )
        return StreamingResponse(
            waiting_generator(),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    async def event_generator():
        """Generate SSE events from execution event log + live queue."""
        subscriber_id = uuid4().hex[:12]
        try:
            # ── Terminal state check ──────────────────────────
            terminal_statuses = ("completed", "failed", "rejected", "cancelled")
            if execution_status in terminal_statuses:
                event_type = execution_status
                display_message = f"Execution already {execution_status}"
                yield _format_sse(
                    event_type,
                    StreamEvent(
                        type=event_type,
                        agent="system",
                        message=display_message,
                    ),
                )
                yield _format_sse(
                    "stream_end",
                    StreamEvent(
                        type="stream_end",
                        agent="system",
                        message="Stream closed — execution already terminal",
                    ),
                )
                return

            # ── Historical replay (reconnect path) ─────────────
            if since:
                logger.info(
                    "Replay requested for execution %s since %s",
                    execution_id,
                    since,
                )
                historical = await event_manager.get_historical_events(
                    execution_id=execution_id,
                    since_timestamp=since,
                )
                if historical:
                    for hist_event in historical:
                        # Skip heartbeat events in replay (they're ephemeral)
                        if hist_event.type == "stream_heartbeat":
                            continue
                        yield _format_sse(hist_event.type, hist_event)

                    logger.debug(
                        "Replayed %d event(s) for execution %s",
                        len(historical),
                        execution_id,
                    )

                # After replay, check if execution already completed
                # during the disconnect
                db_check = await get_db()
                cursor_check = await db_check.execute(
                    "SELECT status FROM executions WHERE id = ?",
                    (execution_id,),
                )
                current_row = await cursor_check.fetchone()
                if current_row:
                    current_status = dict(current_row)["status"]
                    if current_status in terminal_statuses:
                        yield _format_sse(
                            current_status,
                            StreamEvent(
                                type=current_status,
                                agent="system",
                                message=f"Execution already {current_status}",
                            ),
                        )
                        yield _format_sse(
                            "stream_end",
                            StreamEvent(
                                type="stream_end",
                                agent="system",
                                message="Stream closed",
                            ),
                        )
                        return

            # ── Live subscription ──────────────────────────────
            # This continuously yields events (including heartbeats)
            # until the stream ends naturally.
            async for event in event_manager.subscribe(
                execution_id,
                subscriber_id=subscriber_id,
                timeout=SUBSCRIBE_TIMEOUT,
            ):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(
                        "Client disconnected from stream %s (subscriber=%s)",
                        execution_id,
                        subscriber_id,
                    )
                    break

                yield _format_sse(event.type, event)

            # ── Final status check ────────────────────────────
            db_final = await get_db()
            cursor_final = await db_final.execute(
                "SELECT status FROM executions WHERE id = ?",
                (execution_id,),
            )
            final_row = await cursor_final.fetchone()
            if final_row:
                final_status = dict(final_row)["status"]
                if final_status == "completed":
                    yield _format_sse(
                        "workflow_completed",
                        StreamEvent(
                            type="workflow_completed",
                            agent="system",
                            message="Execution completed",
                            data={"executionId": execution_id},
                        ),
                    )
                elif final_status == "failed":
                    yield _format_sse(
                        "workflow_failed",
                        StreamEvent(
                            type="workflow_failed",
                            agent="system",
                            message="Execution failed",
                            data={"executionId": execution_id},
                        ),
                    )
                elif final_status == "cancelled":
                    yield _format_sse(
                        "workflow_cancelled",
                        StreamEvent(
                            type="workflow_cancelled",
                            agent="system",
                            message="Execution cancelled",
                            data={"executionId": execution_id},
                        ),
                    )

            yield _format_sse(
                "stream_end",
                StreamEvent(
                    type="stream_end",
                    agent="system",
                    message="Stream closed",
                ),
            )

        except asyncio.CancelledError:
            logger.info(
                "Stream cancelled for execution %s (subscriber=%s)",
                execution_id,
                subscriber_id,
            )
        except Exception as e:
            logger.error(
                "Stream error for execution %s: %s",
                execution_id,
                e,
            )
            try:
                yield _format_sse(
                    "stream_error",
                    StreamEvent(
                        type="stream_error",
                        agent="system",
                        message=str(e),
                    ),
                )
            except Exception:
                pass
        finally:
            # Queue cleanup is handled by subscribe()'s finally block
            # and/or the orphan sweep. We only explicitly clean up
            # if there's no subscriber left.
            if not await event_manager.has_subscriber(execution_id):
                await event_manager.cleanup(execution_id)
            logger.debug(
                "Stream generator finished for execution %s",
                execution_id,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


# ── SSE Formatter ─────────────────────────────────────────────────


def _format_sse(event_type: str, event: StreamEvent) -> str:
    """
    Format a StreamEvent as an SSE message string.

    SSE format:
      event: <type>\n
      data: <json>\n\n
    """
    data = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _sse_headers() -> dict[str, str]:
    """Standard SSE response headers."""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Content-Type": "text/event-stream",
    }