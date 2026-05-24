"""
Workflow CRUD endpoints.

Save/load lifecycle:
  POST   /api/workflows       → Create workflow from canvas state
  GET    /api/workflows       → List all saved workflows (summaries)
  GET    /api/workflows/{id}  → Load full workflow for canvas
  PUT    /api/workflows/{id}  → Update workflow from canvas save
  DELETE /api/workflows/{id}  → Delete a saved workflow
  POST   /api/workflows/{id}/run → Execute workflow with runtime safety

Execution flow (FIXED):
  1. Route generates execution_id
  2. Route creates DB record (single source of truth)
  3. Route registers task with ExecutionManager
  4. ExecutionManager wraps coroutine in tracked asyncio.Task
  5. Engine uses the SAME execution_id for all events/queues
  6. Frontend connects to /api/executions/{execution_id}/stream
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database.database import get_db
from app.execution.manager import execution_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ── Pydantic Request/Response Models ──────────────────────────────


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


class WorkflowUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None


# ── Helpers ───────────────────────────────────────────────────────


def _row_to_workflow(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row dict to the Workflow API response shape."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "nodes": json.loads(row["nodes"]),
        "edges": json.loads(row["edges"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row dict to the WorkflowSummary API response."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "updatedAt": row["updated_at"],
    }


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("")
async def list_workflows() -> list[dict[str, Any]]:
    """Return all saved workflows (summaries — no node/edge payload)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, name, description, updated_at FROM workflows ORDER BY updated_at DESC"
    )
    rows = await cursor.fetchall()
    return [_row_to_summary(dict(r)) for r in rows]


@router.post("", status_code=201)
async def create_workflow(payload: WorkflowCreate) -> dict[str, Any]:
    """Create a new workflow from canvas state."""
    workflow_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    await db.execute(
        """
        INSERT INTO workflows (id, name, description, nodes, edges, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_id,
            payload.name,
            payload.description,
            json.dumps(payload.nodes),
            json.dumps(payload.edges),
            now,
            now,
        ),
    )
    await db.commit()

    logger.info("Created workflow: %s — %s", workflow_id, payload.name)

    return {
        "id": workflow_id,
        "name": payload.name,
        "description": payload.description,
        "nodes": payload.nodes,
        "edges": payload.edges,
        "createdAt": now,
        "updatedAt": now,
    }


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Get a specific workflow with full nodes/edges for canvas loading."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return _row_to_workflow(dict(row))


@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: str, payload: WorkflowUpdate
) -> dict[str, Any]:
    """Update an existing workflow from canvas save."""
    db = await get_db()

    # Check existence first
    cursor = await db.execute(
        "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
    )
    existing = await cursor.fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    now = datetime.now(timezone.utc).isoformat()

    # Build update dynamically — only include provided fields
    fields: list[str] = []
    values: list[Any] = []

    if payload.name is not None:
        fields.append("name = ?")
        values.append(payload.name)
    if payload.description is not None:
        fields.append("description = ?")
        values.append(payload.description)
    if payload.nodes is not None:
        fields.append("nodes = ?")
        values.append(json.dumps(payload.nodes))
    if payload.edges is not None:
        fields.append("edges = ?")
        values.append(json.dumps(payload.edges))

    fields.append("updated_at = ?")
    values.append(now)
    values.append(workflow_id)

    await db.execute(
        f"UPDATE workflows SET {', '.join(fields)} WHERE id = ?", values
    )
    await db.commit()

    # Return the full updated workflow
    cursor = await db.execute(
        "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
    )
    updated = await cursor.fetchone()

    logger.info("Updated workflow: %s", workflow_id)

    return _row_to_workflow(dict(updated))


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str) -> dict[str, bool]:
    """Delete a workflow and its associated execution records."""
    db = await get_db()

    cursor = await db.execute(
        "SELECT id FROM workflows WHERE id = ?", (workflow_id,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    await db.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
    await db.commit()

    logger.info("Deleted workflow: %s", workflow_id)

    return {"ok": True}


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: str) -> dict[str, Any]:
    """
    Start a workflow execution and return immediately.

    FIXED: Single execution_id is generated by the route and passed to the engine.
    The engine no longer generates its own ID. This ensures the frontend
    receives the correct ID for SSE streaming.

    Flow:
      1. Load workflow from DB (nodes + edges)
      2. Generate execution_id (SINGLE source of truth)
      3. Create DB record
      4. Register task with ExecutionManager (tracked, cancellable)
      5. Return execution_id immediately

    The frontend then opens an EventSource to the stream endpoint.
    """
    db = await get_db()

    # Load workflow
    cursor = await db.execute(
        "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    workflow = _row_to_workflow(dict(row))
    nodes = workflow["nodes"]
    edges = workflow["edges"]

    if not nodes:
        raise HTTPException(
            status_code=400,
            detail="Workflow has no agent nodes. Add agents to the canvas first.",
        )

    logger.info(
        "Run request: workflow=%s (%s) | %d nodes, %d edges",
        workflow_id,
        workflow["name"],
        len(nodes),
        len(edges),
    )

    # Generate SINGLE execution_id — this is the source of truth
    execution_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    # Create execution record in DB (single INSERT)
    # The engine uses this same execution_id — no duplicate
    await db.execute(
        """INSERT INTO executions (id, workflow_id, workflow_name, status, started_at, output)
           VALUES (?, ?, ?, 'running', ?, '{}')""",
        (execution_id, workflow_id, workflow["name"], now),
    )
    await db.commit()

    # Launch execution via ExecutionManager (tracked, cancellable)
    from app.execution.engine import execute_workflow

    try:
        task = await execution_manager.register(
            execution_id=execution_id,
            workflow_id=workflow_id,
            coro=execute_workflow(
                execution_id=execution_id,
                workflow_id=workflow_id,
                nodes=nodes,
                edges=edges,
                workflow_name=workflow["name"],
                workflow_description=workflow.get("description", ""),
            ),
            owner_id="run",
        )

        if task is None:
            # Lock contention — should not happen for new execution
            raise HTTPException(
                status_code=409,
                detail="Could not start execution due to lock contention",
            )

    except RuntimeError as exc:
        # Duplicate execution (should not happen with new UUIDs)
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    logger.info(
        "Started tracked execution: %s for workflow %s",
        execution_id,
        workflow_id,
    )

    # Return immediately with execution_id
    return {
        "executionId": execution_id,
        "workflowId": workflow_id,
        "status": "running",
        "streamUrl": f"/api/executions/{execution_id}/stream",
    }