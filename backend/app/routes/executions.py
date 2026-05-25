"""
Execution History, Replay, and Runtime Control API.

Provides observability into past workflow executions and
runtime control (cancellation):

  GET    /api/executions              → List all executions (with status filters)
  GET    /api/executions/{id}         → Full execution detail + steps + approvals
  GET    /api/executions/{id}/logs    → All persisted event logs for replay
  GET    /api/executions/{id}/summary → Computed execution summary
  POST   /api/executions/{id}/rerun   → Re-run the same workflow with new execution
  POST   /api/executions/{id}/cancel  → Cancel a running execution

Architecture:
  ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
  │  Frontend     │────→│  Executions    │────→│  SQLite DB   │
  │  History UI   │←────│  API Routes    │←────│  + Engine    │
  └──────────────┘     └────────────────┘     └──────────────┘

Each execution is immutable once completed — logs, steps, approvals,
and events are all persisted for replay and inspection.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database.database import get_db
from app.execution.engine import execute_workflow
from app.execution.manager import execution_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/executions", tags=["executions"])


# ── Helpers ───────────────────────────────────────────────────────


def _row_to_execution(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB execution row to the API response format."""
    shared_memory = row.get("shared_memory")
    return {
        "id": row["id"],
        "workflowId": row["workflow_id"],
        "workflowName": row.get("workflow_name", ""),
        "status": row["status"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "error": row.get("error"),
        "output": json.loads(row.get("output", "{}")),
        "summary": row.get("summary", ""),
        "sharedMemory": json.loads(shared_memory) if shared_memory else {},
    }


async def _fetch_steps_for_execution(
    execution_id: str,
) -> list[dict[str, Any]]:
    """Fetch all steps for an execution."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, node_id, agent_name, status, input, output,
                  started_at, completed_at
           FROM execution_steps
           WHERE execution_id = ?
           ORDER BY started_at ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r["id"],
            "nodeId": r["node_id"],
            "agentName": r["agent_name"],
            "status": r["status"],
            "input": r["input"],
            "output": r["output"],
            "startedAt": r["started_at"],
            "completedAt": r["completed_at"],
        }
        for r in rows
    ]


async def _fetch_state_history_for_execution(
    execution_id: str,
) -> list[dict[str, Any]]:
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
            "id": r["id"],
            "timestamp": r["timestamp"],
            "fromState": r["from_state"],
            "toState": r["to_state"],
            "reason": r["reason"],
            "metadata": json.loads(r["metadata"] or "{}"),
        }
        for r in rows
    ]


async def _fetch_approvals_for_execution(
    execution_id: str,
) -> list[dict[str, Any]]:
    """Fetch all approval records for an execution."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, node_id, agent_name, status, requested_at,
                  decided_at, decided_by, notes
           FROM execution_approvals
           WHERE execution_id = ?
           ORDER BY requested_at ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r["id"],
            "nodeId": r["node_id"],
            "agentName": r["agent_name"],
            "status": r["status"],
            "requestedAt": r["requested_at"],
            "decidedAt": r["decided_at"],
            "decidedBy": r["decided_by"],
            "notes": r["notes"],
        }
        for r in rows
    ]


async def _fetch_logs_for_execution(
    execution_id: str,
) -> list[dict[str, Any]]:
    """Fetch all persisted event logs for an execution."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, event_type, timestamp, agent, message, data
           FROM execution_event_logs
           WHERE execution_id = ?
           ORDER BY id ASC""",
        (execution_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r["id"],
            "type": r["event_type"],
            "timestamp": r["timestamp"],
            "agent": r["agent"],
            "message": r["message"],
            "data": json.loads(r["data"]),
        }
        for r in rows
    ]


async def _compute_execution_summary(
    execution_id: str,
) -> dict[str, Any]:
    """Compute a summary for an execution from persisted data."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT status, started_at, completed_at, workflow_name FROM executions WHERE id = ?",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {}

    steps = await _fetch_steps_for_execution(execution_id)
    approvals = await _fetch_approvals_for_execution(execution_id)
    logs = await _fetch_logs_for_execution(execution_id)

    total_steps = len(steps)
    completed_steps = sum(1 for s in steps if s["status"] == "completed")
    failed_steps = sum(1 for s in steps if s["status"] == "failed")

    total_duration = 0.0
    if row["started_at"] and row["completed_at"]:
        try:
            start = datetime.fromisoformat(row["started_at"])
            end = datetime.fromisoformat(row["completed_at"])
            total_duration = (end - start).total_seconds()
        except (ValueError, TypeError):
            pass

    # Build output summary from completed steps
    output_parts = []
    for s in steps:
        if s["status"] == "completed":
            output_parts.append(f"{s['agentName']}: {s['output'][:200]}")

    return {
        "executionId": execution_id,
        "workflowName": row["workflow_name"] or "",
        "status": row["status"],
        "totalSteps": total_steps,
        "completedSteps": completed_steps,
        "failedSteps": failed_steps,
        "totalDurationSeconds": round(total_duration, 2),
        "totalApprovals": len(approvals),
        "pendingApprovals": sum(1 for a in approvals if a["status"] == "pending"),
        "totalEvents": len(logs),
        "outputSummary": "\n".join(output_parts[:5]),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


# ── List / Detail Endpoints ──────────────────────────────────────


@router.get("")
async def list_executions(
    status: str | None = Query(None, description="Filter by status"),
    workflow_id: str | None = Query(None, description="Filter by workflow"),
    search: str | None = Query(None, description="Search in workflow name"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """
    List all executions with optional filters.

    Returns:
    {
        "executions": [...],
        "total": int,
        "limit": int,
        "offset": int
    }
    """
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []

    if status:
        conditions.append("e.status = ?")
        params.append(status)
    if workflow_id:
        conditions.append("e.workflow_id = ?")
        params.append(workflow_id)
    if search:
        conditions.append("e.workflow_name LIKE ?")
        params.append(f"%{search}%")

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Get total count
    count_cursor = await db.execute(
        f"SELECT COUNT(*) FROM executions e{where_clause}", params
    )
    count_row = await count_cursor.fetchone()
    total = count_row[0] if count_row else 0

    # Get paginated results
    cursor = await db.execute(
        f"""SELECT e.id, e.workflow_id, e.workflow_name, e.status,
                   e.started_at, e.completed_at, e.error, e.output, e.summary
            FROM executions e{where_clause}
            ORDER BY e.started_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    executions = [_row_to_execution(dict(r)) for r in rows]

    return {
        "executions": executions,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{execution_id}")
async def get_execution(execution_id: str) -> dict[str, Any]:
    """
    Get full execution detail including:
    - Execution metadata
    - All steps
    - All approval records
    - Execution summary
    """
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, workflow_id, workflow_name, status, started_at,
                  completed_at, error, output, summary, shared_memory
           FROM executions WHERE id = ?""",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    execution = _row_to_execution(dict(row))
    steps = await _fetch_steps_for_execution(execution_id)
    approvals = await _fetch_approvals_for_execution(execution_id)
    state_history = await _fetch_state_history_for_execution(execution_id)
    summary = await _compute_execution_summary(execution_id)

    execution["steps"] = steps
    execution["approvals"] = approvals
    execution["stateHistory"] = state_history
    execution["summary"] = summary

    return execution


# ── Logs Endpoint ────────────────────────────────────────────────


@router.get("/{execution_id}/logs")
async def get_execution_logs(
    execution_id: str,
    limit: int = Query(1000, ge=1, le=10000, description="Max log entries"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """
    Get all persisted event logs for replay.

    Returns logs in chronological order for deterministic replay.
    Each log entry contains the full event data including timing.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM executions WHERE id = ?", (execution_id,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Get total count
    count_cursor = await db.execute(
        "SELECT COUNT(*) FROM execution_event_logs WHERE execution_id = ?",
        (execution_id,),
    )
    count_row = await count_cursor.fetchone()
    total = count_row[0] if count_row else 0

    # Get paginated logs
    logs_cursor = await db.execute(
        """SELECT id, event_type, timestamp, agent, message, data
           FROM execution_event_logs
           WHERE execution_id = ?
           ORDER BY id ASC
           LIMIT ? OFFSET ?""",
        (execution_id, limit, offset),
    )
    rows = await logs_cursor.fetchall()

    logs = [
        {
            "id": r["id"],
            "type": r["event_type"],
            "timestamp": r["timestamp"],
            "agent": r["agent"],
            "message": r["message"],
            "data": json.loads(r["data"]),
        }
        for r in rows
    ]

    return {
        "executionId": execution_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": logs,
    }


# ── Summary Endpoint ─────────────────────────────────────────────


@router.get("/{execution_id}/summary")
async def get_execution_summary(execution_id: str) -> dict[str, Any]:
    """Get a computed summary of an execution."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM executions WHERE id = ?", (execution_id,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    return await _compute_execution_summary(execution_id)


# ── Rerun Endpoint (FIXED) ───────────────────────────────────────


@router.post("/{execution_id}/rerun")
async def rerun_execution(execution_id: str) -> dict[str, Any]:
    """
    Re-run a completed/failed execution.

    FIXED: Now returns the REAL execution ID instead of a synthetic one.
    The rerun generates a new execution_id and registers it with
    ExecutionManager, so the frontend can connect to SSE immediately.

    Returns the same shape as POST /api/workflows/{id}/run:
    {
        "executionId": "...",
        "workflowId": "...",
        "status": "running",
        "streamUrl": "..."
    }
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT workflow_id, workflow_name FROM executions WHERE id = ?",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    workflow_id = row["workflow_id"]

    # Load the workflow configuration
    workflow_cursor = await db.execute(
        "SELECT name, description, nodes, edges FROM workflows WHERE id = ?",
        (workflow_id,),
    )
    workflow_row = await workflow_cursor.fetchone()
    if workflow_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow {workflow_id} not found (may have been deleted)",
        )

    nodes = json.loads(workflow_row["nodes"])
    edges = json.loads(workflow_row["edges"])

    if not nodes:
        raise HTTPException(
            status_code=400,
            detail="Workflow has no nodes — nothing to execute",
        )

    workflow_name = workflow_row["name"] or "Untitled Workflow"
    workflow_description = workflow_row["description"] or ""

    # Generate NEW execution_id for the rerun
    new_execution_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    # Create execution record in DB
    await db.execute(
        """INSERT INTO executions (id, workflow_id, workflow_name, status, started_at, output)
           VALUES (?, ?, ?, 'running', ?, '{}')""",
        (new_execution_id, workflow_id, workflow_name, now),
    )
    await db.commit()

    # Register with ExecutionManager and launch
    from app.execution.engine import execute_workflow

    try:
        task = await execution_manager.register(
            execution_id=new_execution_id,
            workflow_id=workflow_id,
            coro=execute_workflow(
                execution_id=new_execution_id,
                workflow_id=workflow_id,
                nodes=nodes,
                edges=edges,
                workflow_name=workflow_name,
                workflow_description=workflow_description,
            ),
            owner_id="rerun",
        )

        if task is None:
            raise HTTPException(
                status_code=409,
                detail="Could not start rerun due to lock contention",
            )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    logger.info(
        "Rerun initiated: new execution %s (from original %s, workflow %s)",
        new_execution_id,
        execution_id,
        workflow_id,
    )

    # Return the REAL new execution ID — not a synthetic one
    return {
        "executionId": new_execution_id,
        "workflowId": workflow_id,
        "status": "running",
        "streamUrl": f"/api/executions/{new_execution_id}/stream",
    }


# ── Cancel Endpoint (NEW) ────────────────────────────────────────


@router.post("/{execution_id}/cancel")
async def cancel_execution(execution_id: str) -> dict[str, Any]:
    """
    Cancel a running execution.

    The execution must be in 'running' or 'waiting_approval' status.
    On cancellation:
    1. Cancellation signal is set
    2. The asyncio task is cancelled
    3. DB status is updated to 'cancelled'
    4. SSE queue is cleaned up

    Returns the execution ID and final status.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status FROM executions WHERE id = ?",
        (execution_id,),
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    current_status = dict(row)["status"]

    # Check if execution can be cancelled
    cancellable_statuses = ("running", "waiting_approval")
    if current_status not in cancellable_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"Execution is not cancellable (current status: {current_status}). "
                   f"Only 'running' or 'waiting_approval' executions can be cancelled.",
        )

    # Initiate cancellation via ExecutionManager
    success = await execution_manager.cancel(
        execution_id=execution_id,
        mark_db_cancelled=True,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Execution not found in runtime registry (may have just completed)",
        )

    logger.info("Execution %s cancelled successfully", execution_id)

    return {
        "executionId": execution_id,
        "status": "cancelled",
        "detail": "Execution cancelled",
    }


# ── Active Executions Endpoint (NEW) ─────────────────────────────


@router.get("/active/list")
async def list_active_executions() -> dict[str, Any]:
    """
    List all currently active (running/cancelling) executions.

    Returns runtime state from the ExecutionManager.
    Useful for frontend to detect orphaned or in-flight executions
    after page refresh.
    """
    active = execution_manager.list_active()
    return {
        "active": active,
        "count": len(active),
    }