"""
Approval endpoints — human-in-the-loop workflow supervision.

When a workflow hits an approval node, execution pauses and
the frontend shows the approval UI. The user can then:

  POST /api/executions/{id}/approve  → resume execution
  POST /api/executions/{id}/reject   → cancel execution

Both endpoints are idempotent — calling them on a non-paused
execution returns a 409 Conflict.

Race safety (FIXED):
  - Per-execution ExecutionLock prevents concurrent approve/reject
  - Optimistic DB check: status must be 'waiting_approval'
  - Atomic status transition prevents double-processing

Flow:
  1. Engine pauses → status = 'waiting_approval', checkpoint saved
  2. User sees approval UI → clicks Approve/Reject
  3. POST /approve or /reject calls resume_workflow() or reject_execution()
  4. Engine continues from next node after approval gate
  5. New SSE stream is created for the resumed execution
"""

import logging

from fastapi import APIRouter, HTTPException

from app.database.database import get_db
from app.execution.engine import resume_workflow, reject_execution
from app.execution.manager import execution_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/executions", tags=["approvals"])


async def _verify_waiting_approval(execution_id: str) -> dict:
    """
    Verify an execution is in 'waiting_approval' status.

    Uses optimistic lock via execution_manager to prevent
    concurrent approve/reject race.

    Returns:
        The execution row dict.

    Raises:
        HTTPException 404 or 409.
    """
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, status FROM executions WHERE id = ?",
        (execution_id,),
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    status = dict(row)["status"]

    if status != "waiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Execution is not waiting for approval (current status: {status}). "
                   f"Only 'waiting_approval' executions can be acted upon.",
        )

    return dict(row)


@router.post("/{execution_id}/approve")
async def approve_execution(execution_id: str) -> dict:
    """
    Approve a paused execution and resume it.

    The execution must be in 'waiting_approval' status.
    On success, execution resumes from the node after the approval gate
    and a new SSE stream becomes available.

    Thread-safe: uses ExecutionLock to prevent concurrent approve/reject.
    """
    # Verify preconditions
    await _verify_waiting_approval(execution_id)

    # Acquire per-execution lock to prevent race with reject
    acquired = await execution_manager._execution_lock.acquire(
        execution_id, "approve"
    )
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="Another approval action is already in progress for this execution",
        )

    try:
        # Re-verify status after acquiring lock
        db = await get_db()
        cursor = await db.execute(
            "SELECT status FROM executions WHERE id = ?",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if not row or dict(row)["status"] != "waiting_approval":
            raise HTTPException(
                status_code=409,
                detail="Execution status changed before approval could be processed",
            )

        result = await resume_workflow(execution_id)
        logger.info("Execution %s approved and resumed", execution_id)

        return {
            "executionId": execution_id,
            "status": result.get("status", "running"),
            "streamUrl": f"/api/executions/{execution_id}/stream",
        }
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("Failed to resume execution %s: %s", execution_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume execution: {e}",
        )
    finally:
        execution_manager._execution_lock.release(execution_id)


@router.post("/{execution_id}/reject")
async def reject_execution_endpoint(execution_id: str) -> dict:
    """
    Reject a paused execution and cancel it.

    The execution must be in 'waiting_approval' status.
    On rejection, the execution is marked as 'rejected' and
    final events are streamed via SSE.

    Thread-safe: uses ExecutionLock to prevent concurrent approve/reject.
    """
    # Verify preconditions
    await _verify_waiting_approval(execution_id)

    # Acquire per-execution lock to prevent race with approve
    acquired = await execution_manager._execution_lock.acquire(
        execution_id, "reject"
    )
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="Another approval action is already in progress for this execution",
        )

    try:
        # Re-verify status after acquiring lock
        db = await get_db()
        cursor = await db.execute(
            "SELECT status FROM executions WHERE id = ?",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if not row or dict(row)["status"] != "waiting_approval":
            raise HTTPException(
                status_code=409,
                detail="Execution status changed before rejection could be processed",
            )

        result = await reject_execution(execution_id)
        logger.info("Execution %s rejected", execution_id)

        return {
            "executionId": execution_id,
            "status": "rejected",
            "detail": result.get("error", "Execution rejected"),
        }
    except Exception as e:
        logger.error("Failed to reject execution %s: %s", execution_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reject execution: {e}",
        )
    finally:
        execution_manager._execution_lock.release(execution_id)