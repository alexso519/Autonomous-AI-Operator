"""
Runtime state machine for workflow execution.

This module records deterministic execution lifecycle transitions
and persists them to the database for offline inspection.

NOTE: New code should prefer LifecycleManager from runtime_lifecycle.py
for transition validation, phase tracking, and replay support.
The functions here remain as the persistence layer and backward-compatible API.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


class RuntimeState(str, Enum):
    """A stable runtime state for workflow execution."""

    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REJECTED = "rejected"
    ORPHANED = "orphaned"


_ALLOWED_STATE_TRANSITIONS: dict[RuntimeState, set[RuntimeState]] = {
    RuntimeState.CREATED: {
        RuntimeState.PENDING,
        RuntimeState.RUNNING,
        RuntimeState.CANCELLED,
        RuntimeState.FAILED,
    },
    RuntimeState.PENDING: {
        RuntimeState.RUNNING,
        RuntimeState.CANCELLED,
        RuntimeState.FAILED,
    },
    RuntimeState.RUNNING: {
        RuntimeState.WAITING_APPROVAL,
        RuntimeState.COMPLETED,
        RuntimeState.CANCELLED,
        RuntimeState.FAILED,
    },
    RuntimeState.WAITING_APPROVAL: {
        RuntimeState.RUNNING,
        RuntimeState.REJECTED,
        RuntimeState.CANCELLED,
    },
    RuntimeState.COMPLETED: set(),
    RuntimeState.CANCELLED: set(),
    RuntimeState.FAILED: set(),
    RuntimeState.REJECTED: set(),
    RuntimeState.ORPHANED: set(),
}


def _normalize_state(state: RuntimeState | str | None) -> RuntimeState | None:
    if state is None:
        return None
    if isinstance(state, RuntimeState):
        return state
    return RuntimeState(state)


def _is_valid_transition(
    from_state: RuntimeState | None,
    to_state: RuntimeState,
) -> bool:
    if from_state is None or from_state == RuntimeState.CREATED:
        return True
    allowed = _ALLOWED_STATE_TRANSITIONS.get(from_state, set())
    return to_state in allowed


async def _insert_transition(
    execution_id: str,
    from_state: RuntimeState | None,
    to_state: RuntimeState,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db = await get_db()
    transition_id = uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata or {})
    await db.execute(
        """INSERT INTO execution_state_transitions
           (id, execution_id, timestamp, from_state, to_state, reason, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            transition_id,
            execution_id,
            timestamp,
            from_state.value if from_state else RuntimeState.CREATED.value,
            to_state.value,
            reason or "",
            metadata_json,
        ),
    )
    await db.commit()


async def get_latest_state(execution_id: str) -> RuntimeState | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT to_state FROM execution_state_transitions
           WHERE execution_id = ?
           ORDER BY timestamp DESC, id DESC LIMIT 1""",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if row:
        try:
            return RuntimeState(row["to_state"])
        except ValueError:
            pass

    cursor = await db.execute(
        "SELECT status FROM executions WHERE id = ?", (execution_id,)
    )
    row = await cursor.fetchone()
    if row:
        status = row["status"]
        if status in RuntimeState._value2member_map_:
            return RuntimeState(status)
    return None


async def get_state_history(execution_id: str) -> list[dict[str, Any]]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, timestamp, from_state, to_state, reason, metadata
           FROM execution_state_transitions
           WHERE execution_id = ?
           ORDER BY timestamp ASC, id ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "fromState": row["from_state"],
            "toState": row["to_state"],
            "reason": row["reason"],
            "metadata": json.loads(row["metadata"] or "{}"),
        }
        for row in rows
    ]


async def transition_execution_state(
    execution_id: str,
    to_state: RuntimeState,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    from_state: RuntimeState | str | None = None,
) -> RuntimeState:
    current_state = await get_latest_state(execution_id)
    normalized_to = _normalize_state(to_state)
    if normalized_to is None:
        raise ValueError(f"Invalid target runtime state: {to_state}")

    normalized_from = _normalize_state(from_state) if from_state else current_state
    if normalized_from is None:
        normalized_from = RuntimeState.CREATED

    if normalized_from == normalized_to:
        logger.debug(
            "Runtime state already %s for execution %s",
            normalized_to.value,
            execution_id,
        )
        return normalized_to

    if not _is_valid_transition(normalized_from, normalized_to):
        logger.warning(
            "Invalid runtime state transition from %s to %s for %s",
            normalized_from.value,
            normalized_to.value,
            execution_id,
        )

    await _insert_transition(
        execution_id=execution_id,
        from_state=normalized_from,
        to_state=normalized_to,
        reason=reason,
        metadata=metadata,
    )

    try:
        db = await get_db()
        await db.execute(
            "UPDATE executions SET status = ? WHERE id = ?",
            (normalized_to.value, execution_id),
        )
        await db.commit()
    except Exception as exc:
        logger.warning(
            "Failed to update execution status for %s: %s",
            execution_id,
            exc,
        )

    return normalized_to


async def init_execution_state(
    execution_id: str,
    workflow_id: str,
    initial_state: RuntimeState = RuntimeState.PENDING,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeState:
    if metadata is None:
        metadata = {}
    metadata["workflowId"] = workflow_id
    current_state = await get_latest_state(execution_id)
    if current_state is not None:
        return await transition_execution_state(
            execution_id=execution_id,
            to_state=initial_state,
            reason=reason or "Execution initialized",
            metadata=metadata,
            from_state=current_state,
        )

    return await transition_execution_state(
        execution_id=execution_id,
        to_state=initial_state,
        reason=reason or "Execution initialized",
        metadata=metadata,
        from_state=RuntimeState.CREATED,
    )
