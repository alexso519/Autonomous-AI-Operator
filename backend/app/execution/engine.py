"""
Sequential workflow execution engine with live SSE streaming
and human-in-the-loop approval checkpoints.

Execution model:
  for node in sorted_nodes:
    if node is approval:
      pause → save checkpoint → emit approval_requested → wait
    else:
      agent.execute(task, context)
      emit output

Approval lifecycle:
  Agent → Agent → Approval (pause) → [user approves] → Agent → Complete

When an approval node is encountered:
  1. Checkpoint is saved (context, position, workflow metadata)
  2. Execution status → 'waiting_approval'
  3. approval_requested event emitted
  4. Engine returns early — execution is paused

On approve:
  1. Load checkpoint
  2. Status → 'approved' → 'running'
  3. approval_granted + execution_resumed events
  4. Continue execution from next node

On reject:
  1. Load checkpoint
  2. Status → 'rejected'
  3. approval_rejected event emitted
  4. Execution ends

Runtime safety:
  - execution_id is OWNED by the route layer — passed in, not generated
  - Cancellation checkpoints at every agent boundary
  - Per-agent timeout enforced via ExecutionManager.run_agent_in_thread()
  - SSE queue cleanup on every exit path
  - No duplicated execution loop (execute_workflow and resume_workflow
    share _execute_agent_step)
"""

import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from crewai import Task as CrewTask

from app.database.database import get_db
from app.execution.agent_factory import build_agent
from app.execution.context_manager import ContextManager
from app.execution.manager import execution_manager
from app.execution.reflection_service import ReflectionService, ReplanningService
from app.execution.runtime_state import (
    RuntimeState,
    init_execution_state,
    transition_execution_state,
)
from app.streaming.event_manager import event_manager, StreamEvent

logger = logging.getLogger(__name__)

# Default per-agent timeout (seconds)
_DEFAULT_AGENT_TIMEOUT = 300  # 5 minutes


# ── Helpers ───────────────────────────────────────────────────────


def _sort_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort nodes into execution order: top-to-bottom, left-to-right."""
    if not nodes:
        return []

    def _position_key(n: dict[str, Any]) -> tuple[float, float]:
        pos = n.get("position", {})
        return (pos.get("y", 0), pos.get("x", 0))

    return sorted(nodes, key=_position_key)


def _is_approval_node(node: dict[str, Any]) -> bool:
    """Check if a node is an approval gate."""
    node_type = node.get("type", node.get("data", {}).get("nodeType", "agent"))
    return node_type == "approval"


def _resolve_prompt(node_data: dict[str, Any], context: str) -> str:
    """Build the task prompt for an agent node."""
    goal = node_data.get("goal", "Complete the assigned task.")
    label = node_data.get("label", "Agent")

    parts = [f"You are acting as: {label}", "", f"Your goal: {goal}"]

    if context.strip():
        parts.extend(["", "--- Context from previous agents ---", context])

    parts.extend([
        "",
        "--- Instructions ---",
        "Please complete your goal using the context provided above.",
        "Provide a thorough, well-structured response.",
    ])

    return "\n".join(parts)


# ── Streaming event helpers ───────────────────────────────────────


async def _emit(
    execution_id: str,
    event_type: str,
    agent: str,
    message: str,
    **data: Any,
) -> None:
    """Emit a typed event to the execution's stream AND persist it."""
    # Check cancellation before emitting (saves unnecessary DB writes)
    if execution_manager.is_cancellation_requested(execution_id):
        return

    event = StreamEvent(
        type=event_type,
        agent=agent,
        message=message,
        data=data,
    )
    await event_manager.emit(execution_id, event)

    # Persist event to execution_event_logs
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO execution_event_logs
               (execution_id, event_type, timestamp, agent, message, data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                execution_id,
                event_type,
                event.timestamp,
                agent,
                message[:1000],
                json.dumps(data),
            ),
        )
        await db.commit()
    except Exception:
        logger.exception(
            "Failed to persist event for execution %s", execution_id
        )


async def _end_stream(execution_id: str) -> None:
    """Send sentinel to end the SSE stream."""
    if not execution_manager.is_cancellation_requested(execution_id):
        await event_manager.emit(execution_id, None)


async def _record_runtime_state(
    execution_id: str,
    to_state: RuntimeState,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    from_state: RuntimeState | str | None = None,
) -> None:
    """Persist a runtime state transition without breaking execution."""
    try:
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=to_state,
            reason=reason,
            metadata=metadata,
            from_state=from_state,
        )
    except Exception as exc:
        logger.warning(
            "Could not persist runtime state transition for %s: %s",
            execution_id,
            exc,
        )


# ── Cancellation check ───────────────────────────────────────────


async def _check_cancelled(
    execution_id: str,
    node_name: str = "",
) -> bool:
    """
    Check if cancellation was requested. If so, emit cancelled event
    and return True.

    Call this at agent boundaries so cancellation is responsive.
    """
    if execution_manager.is_cancellation_requested(execution_id):
        logger.info(
            "Execution %s cancelled at boundary (node=%s)",
            execution_id,
            node_name,
        )
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.CANCELLED,
            reason=f"Execution cancelled at node '{node_name}'",
            metadata={"nodeName": node_name},
        )
        try:
            await _emit(
                execution_id,
                "workflow_cancelled",
                "system",
                f"Execution cancelled at '{node_name}'",
                executionId=execution_id,
            )
        except Exception:
            pass

        # Update DB
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """UPDATE executions
                   SET status = 'cancelled', completed_at = ?
                   WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled', 'rejected')""",
                (now, execution_id),
            )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "Failed to update DB for cancelled exec %s: %s",
                execution_id,
                exc,
            )

        return True
    return False


# ── Checkpoint save/load ─────────────────────────────────────────


async def _save_checkpoint(
    execution_id: str,
    ctx: ContextManager,
    sorted_nodes: list[dict[str, Any]],
    current_idx: int,
    workflow_id: str,
    workflow_name: str,
    workflow_description: str,
    logs: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """
    Persist execution checkpoint so we can resume after approval.
    The checkpoint stores everything needed to continue execution
    from the node AFTER the current index.
    """
    checkpoint = {
        "current_idx": current_idx,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "workflow_description": workflow_description,
        "nodes": sorted_nodes,
        "edges": edges,
        "context_state": ctx.get_state(),
        "logs": logs,
        "steps": steps,
    }

    db = await get_db()
    await db.execute(
        "UPDATE executions SET checkpoint = ? WHERE id = ?",
        (json.dumps(checkpoint), execution_id),
    )
    await db.commit()
    logger.info(
        "Checkpoint saved for execution %s at index %d",
        execution_id,
        current_idx,
    )


async def _load_checkpoint(
    execution_id: str,
) -> dict[str, Any] | None:
    """Load a saved checkpoint for an execution."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT checkpoint FROM executions WHERE id = ?",
        (execution_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    raw = dict(row)["checkpoint"]
    if not raw:
        return None

    return json.loads(raw)


# ── Approval node handler ─────────────────────────────────────────


async def _handle_approval_node(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    sorted_nodes: list[dict[str, Any]],
    ctx: ContextManager,
    workflow_id: str,
    workflow_name: str,
    workflow_description: str,
    logs: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    agent_count: int,
) -> None:
    """
    Handle an approval gate node:
    1. Save checkpoint
    2. Create approval record
    3. Update status to waiting_approval
    4. Emit approval_requested event
    5. End the stream
    """
    node_id = node.get("id", f"approval_{idx}")
    node_data = node.get("data", {})
    agent_name = node_data.get("label", "Approval Gate")

    now = datetime.now(timezone.utc).isoformat()
    approval_id = uuid.uuid4().hex[:12]

    # Add a checkpoint memory marker for the approval pause
    ctx.add_approval_decision(
        node_id=node_id,
        decision="pending",
        notes=node_data.get("reason", "Approval required"),
    )
    await ctx.persist()

    await _record_runtime_state(
        execution_id=execution_id,
        to_state=RuntimeState.WAITING_APPROVAL,
        reason="Execution paused for human approval",
        metadata={
            "nodeId": node_id,
            "agentName": agent_name,
            "approvalReason": node_data.get(
                "reason", "Proceed with workflow execution?"
            ),
        },
    )

    # Save checkpoint
    await _save_checkpoint(
        execution_id=execution_id,
        ctx=ctx,
        sorted_nodes=sorted_nodes,
        current_idx=idx,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        workflow_description=workflow_description,
        logs=logs,
        steps=steps,
        edges=edges,
    )

    # Create approval record
    db = await get_db()
    await db.execute(
        """INSERT INTO execution_approvals
           (id, execution_id, node_id, agent_name, status, requested_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (approval_id, execution_id, node_id, agent_name, now),
    )

    # Update execution status to waiting_approval
    await db.execute(
        "UPDATE executions SET status = 'waiting_approval' WHERE id = ?",
        (execution_id,),
    )
    await db.commit()

    # Emit: approval_requested
    await _emit(
        execution_id,
        "approval_requested",
        agent_name,
        f'⏸️ Approval required: "{agent_name}" — waiting for human decision',
        nodeId=node_id,
        approvalId=approval_id,
        step=idx,
        totalSteps=agent_count,
        reason=node_data.get("reason", "Proceed with workflow execution?"),
    )

    approval_log = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": now,
        "level": "approval",
        "agentName": agent_name,
        "content": f'⏸️ Approval required: "{agent_name}" — waiting for human decision',
    }
    logs.append(approval_log)

    await _emit(
        execution_id,
        "log",
        "system",
        f'⏸️ Execution paused — waiting for approval at "{agent_name}"',
        logId=approval_log["id"],
        level="approval",
    )

    logger.info(
        "Approval paused: execution=%s node=%s approval_id=%s",
        execution_id,
        node_id,
        approval_id,
    )


# ── Shared agent execution step ───────────────────────────────────


async def _execute_agent_step(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    agent_count: int,
    ctx: ContextManager,
    logs: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    agent_timeout: int = _DEFAULT_AGENT_TIMEOUT,
) -> tuple[str, str | None]:
    """
    Execute a single agent node and return (status, error).

    This is the SHARED execution logic used by both execute_workflow
    and resume_workflow — eliminating the ~200 lines of duplication.

    Returns:
        ("completed", None) on success
        ("failed", error_message) on failure
        ("cancelled", None) if cancellation was requested
    """
    # Check cancellation before starting agent
    if await _check_cancelled(execution_id, node.get("data", {}).get("label", "")):
        return "cancelled", None

    node_id = node.get("id", f"node_{idx}")
    node_data = node.get("data", {})
    agent_name = node_data.get("label", node_data.get("role", f"Node {idx}"))
    step_id = uuid.uuid4().hex[:12]
    step_now = datetime.now(timezone.utc).isoformat()

    db = await get_db()

    # Create execution step record
    await db.execute(
        """INSERT INTO execution_steps
           (id, execution_id, node_id, agent_name, status, input, started_at)
           VALUES (?, ?, ?, ?, 'running', ?, ?)""",
        (step_id, execution_id, node_id, agent_name, "", step_now),
    )
    await db.commit()

    # Emit: agent_started
    await _emit(
        execution_id,
        "agent_started",
        agent_name,
        f"[{idx}/{agent_count}] Activating {agent_name}...",
        nodeId=node_id,
        step=idx,
        totalSteps=agent_count,
        goal=node_data.get("goal", "No goal specified"),
    )

    log_entry = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": step_now,
        "level": "info",
        "agentName": "system",
        "content": f"[{idx}/{agent_count}] Activating {agent_name}...",
    }
    logs.append(log_entry)

    await _emit(
        execution_id,
        "log",
        "system",
        f"[{idx}/{agent_count}] Activating {agent_name}...",
        logId=log_entry["id"],
        level="info",
    )

    log_entry2 = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "agent",
        "agentName": agent_name,
        "content": f"Starting task: {node_data.get('goal', 'No goal specified')}",
    }
    logs.append(log_entry2)

    await _emit(
        execution_id,
        "log",
        agent_name,
        f"Starting task: {node_data.get('goal', 'No goal specified')}",
        logId=log_entry2["id"],
        level="agent",
    )

    try:
        # Build agent from node data
        agent = build_agent(node_data)

        # Build task with context
        combined_context = ctx.get_combined_context()
        prompt = _resolve_prompt(node_data, combined_context)

        task = CrewTask(
            description=prompt,
            expected_output="A detailed response addressing the goal.",
            agent=agent,
        )

        # Emit: thinking
        await _emit(
            execution_id,
            "thinking",
            agent_name,
            f"⏳ {agent_name} is thinking...",
            nodeId=node_id,
        )

        log_entry3 = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "agentName": "system",
            "content": f"⏳ {agent_name} is thinking...",
        }
        logs.append(log_entry3)

        await _emit(
            execution_id,
            "log",
            "system",
            f"⏳ {agent_name} is thinking...",
            logId=log_entry3["id"],
            level="info",
        )

        # Execute agent with timeout and cancellation support
        # Uses ExecutionManager's thread pool for cancellable execution
        output_text = await _run_agent_with_timeout(
            execution_id=execution_id,
            agent=agent,
            task=task,
            timeout=agent_timeout,
        )

        completed_now = datetime.now(timezone.utc).isoformat()
        ctx.add_output(node_id, agent_name, output_text)
        await ctx.persist()

        # Check cancellation after agent execution
        if await _check_cancelled(
            execution_id, agent_name
        ):
            # Update step as cancelled
            await db.execute(
                """UPDATE execution_steps
                   SET status = 'cancelled', output = ?, completed_at = ?
                   WHERE id = ?""",
                (output_text[:500], completed_now, step_id),
            )
            await db.commit()
            return "cancelled", None

        # Update step record
        await db.execute(
            """UPDATE execution_steps
               SET status = 'completed', output = ?, completed_at = ?
               WHERE id = ?""",
            (output_text, completed_now, step_id),
        )
        await db.commit()

        # Emit: output
        await _emit(
            execution_id,
            "output",
            agent_name,
            output_text[:500],
            nodeId=node_id,
            output=output_text,
            truncated=len(output_text) > 500,
        )

        log_entry4 = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": completed_now,
            "level": "output",
            "agentName": agent_name,
            "content": output_text[:500],
        }
        logs.append(log_entry4)

        await _emit(
            execution_id,
            "log",
            agent_name,
            output_text[:500],
            logId=log_entry4["id"],
            level="output",
        )

        # Emit: agent_completed
        await _emit(
            execution_id,
            "agent_completed",
            agent_name,
            f"✓ {agent_name} completed",
            nodeId=node_id,
            duration=(
                datetime.now(timezone.utc)
                - datetime.fromisoformat(step_now)
            ).total_seconds(),
        )

        log_entry5 = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "agentName": "system",
            "content": f"✓ {agent_name} completed",
        }
        logs.append(log_entry5)

        await _emit(
            execution_id,
            "log",
            "system",
            f"✓ {agent_name} completed",
            logId=log_entry5["id"],
            level="info",
        )

        steps.append({
            "nodeId": node_id,
            "agentName": agent_name,
            "status": "completed",
            "input": prompt,
            "output": output_text,
            "startedAt": step_now,
            "completedAt": completed_now,
        })

        logger.info(
            "Agent %s completed (step %d/%d)",
            agent_name,
            idx,
            agent_count,
        )
        return "completed", None

    except asyncio.CancelledError:
        logger.info(
            "Agent %s cancelled (execution %s)", agent_name, execution_id
        )
        # Step will be marked as cancelled in DB
        return "cancelled", None

    except Exception as exc:
        failed_now = datetime.now(timezone.utc).isoformat()
        error_msg = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()

        # Update step record as failed
        await db.execute(
            """UPDATE execution_steps
               SET status = 'failed', output = ?, completed_at = ?
               WHERE id = ?""",
            (error_msg, failed_now, step_id),
        )
        await db.commit()

        # Emit: error events
        error_log = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": failed_now,
            "level": "error",
            "agentName": agent_name,
            "content": error_msg,
        }
        logs.append(error_log)

        await _emit(
            execution_id,
            "log",
            agent_name,
            error_msg,
            logId=error_log["id"],
            level="error",
        )

        system_error_log = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": failed_now,
            "level": "error",
            "agentName": "system",
            "content": f"✕ {agent_name} failed — aborting workflow",
        }
        logs.append(system_error_log)

        await _emit(
            execution_id,
            "log",
            "system",
            f"✕ {agent_name} failed — aborting workflow",
            logId=system_error_log["id"],
            level="error",
        )

        steps.append({
            "nodeId": node_id,
            "agentName": agent_name,
            "status": "failed",
            "input": prompt,
            "output": error_msg,
            "startedAt": step_now,
            "completedAt": failed_now,
        })

        logger.error("Agent %s failed: %s\n%s", agent_name, error_msg, tb)
        return "failed", error_msg


# ── Timeout wrapper ───────────────────────────────────────────────


async def _run_agent_with_timeout(
    execution_id: str,
    agent: Any,
    task: Any,
    timeout: int = _DEFAULT_AGENT_TIMEOUT,
) -> str:
    """
    Run a single agent with timeout and cancellation support.

    Uses ExecutionManager.run_agent_in_thread() for proper thread
    management and zombie tracking.
    """
    # Note: agent.execute_task is synchronous (CrewAI).
    # We run it in a thread to avoid blocking the event loop.
    def _execute() -> str:
        output = agent.execute_task(task)
        return str(output) if output else "(no output)"

    try:
        output_text = await execution_manager.run_agent_in_thread(
            execution_id=execution_id,
            fn=_execute,
            timeout=float(timeout),
        )
        return output_text
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Agent execution timed out after {timeout}s"
        ) from None


async def _run_reflection_and_replanning(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    sorted_nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ctx: ContextManager,
    logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate output quality and insert recovery steps when needed."""
    finding = ReflectionService.inspect_output(node, ctx)
    if not finding.should_replan:
        if finding.action == "max_retries_reached":
            await _emit(
                execution_id,
                "reflection_limit_reached",
                "system",
                f"Reflection limit reached for {finding.agent_name}: {finding.reason}",
                nodeId=finding.node_id,
                issues=finding.issues,
            )
        return sorted_nodes

    sorted_nodes = ReplanningService.insert_recovery_steps(
        sorted_nodes=sorted_nodes,
        edges=edges,
        node_index=idx - 1,
        node=node,
        finding=finding,
        ctx=ctx,
    )
    await ctx.persist()

    message = (
        f"Reflection detected weak output for {finding.agent_name}. "
        f"Injected recovery workflow steps: {finding.action}."
    )
    await _emit(
        execution_id,
        "reflection_action",
        "system",
        message,
        nodeId=finding.node_id,
        issues=finding.issues,
        action=finding.action,
        retryCount=finding.retry_count + 1,
    )

    logs.append({
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "warning",
        "agentName": "system",
        "content": message,
    })
    return sorted_nodes


# ── Core execution ──────────────────────────────────────────────


async def execute_workflow(
    execution_id: str,
    workflow_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    workflow_name: str = "Untitled Workflow",
    workflow_description: str = "",
    agent_timeout: int = _DEFAULT_AGENT_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute a workflow sequentially with live SSE streaming.

    IMPORTANT: execution_id is OWNED by the caller (route layer).
    This function does NOT generate its own execution ID.
    The route creates the DB record before calling this function.

    Supports approval gates: when an approval node is encountered,
    execution pauses, a checkpoint is saved, and the engine waits
    for human approval via approve/reject endpoints.

    Lifecycle events emitted:
      1. workflow_started
      2. agent_started (per agent)
      3. log (various info messages)
      4. thinking (agent is processing)
      5. output (agent produced result)
      6. agent_completed (per agent, after output saved)
      7. approval_requested (when approval gate reached)
      8. workflow_completed | workflow_failed

    Args:
        execution_id: Execution identifier (pre-generated by route).
        workflow_id: The workflow's UUID.
        nodes: Canvas node list.
        edges: Canvas edge list.
        workflow_name: Display name.
        workflow_description: Initial context.
        agent_timeout: Per-agent timeout in seconds.

    Returns:
        Final execution result dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()

    # Sort nodes into execution order
    sorted_nodes = _sort_nodes(nodes)
    agent_count = len(sorted_nodes)

    logger.info(
        "Executing workflow: %s (%s) | %d nodes | exec=%s",
        workflow_id,
        workflow_name,
        agent_count,
        execution_id,
    )

    # Create execution stream
    await event_manager.create_stream(execution_id)

    # Initialize context
    ctx = ContextManager(execution_id=execution_id)
    ctx.set_context(workflow_description)
    await ctx.persist()

    await init_execution_state(
        execution_id=execution_id,
        workflow_id=workflow_id,
        initial_state=RuntimeState.RUNNING,
        reason="Execution started",
        metadata={
            "workflowName": workflow_name,
            "agentCount": agent_count,
        },
    )

    logs: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    final_status = "completed"
    final_error: str | None = None

    # Emit: workflow_started
    await _emit(
        execution_id,
        "workflow_started",
        "system",
        f"Starting workflow: {workflow_name} ({agent_count} nodes)",
        workflowId=workflow_id,
        workflowName=workflow_name,
        agentCount=agent_count,
    )

    logs.append({
        "id": uuid.uuid4().hex[:12],
        "timestamp": now,
        "level": "info",
        "agentName": "system",
        "content": f"Starting workflow: {workflow_name} ({agent_count} nodes)",
    })

    # Execute each node sequentially
    for idx, node in enumerate(sorted_nodes, start=1):
        node_data = node.get("data", {})
        agent_name = node_data.get(
            "label", node_data.get("role", f"Node {idx}")
        )

        # Check cancellation before each node
        if await _check_cancelled(execution_id, agent_name):
            final_status = "cancelled"
            break

        # Check for approval gate
        if _is_approval_node(node):
            await _handle_approval_node(
                execution_id=execution_id,
                node=node,
                idx=idx,
                sorted_nodes=sorted_nodes,
                ctx=ctx,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                workflow_description=workflow_description,
                logs=logs,
                steps=steps,
                edges=edges,
                agent_count=agent_count,
            )
            # Return early — execution is paused waiting for approval
            # The stream ends here; it will be re-created on resume
            await _end_stream(execution_id)
            return {
                "executionId": execution_id,
                "status": "waiting_approval",
                "steps": steps,
                "logs": logs,
                "error": None,
            }

        # Execute agent step using shared logic
        status, error = await _execute_agent_step(
            execution_id=execution_id,
            node=node,
            idx=idx,
            agent_count=agent_count,
            ctx=ctx,
            logs=logs,
            steps=steps,
            agent_timeout=agent_timeout,
        )

        if status == "cancelled":
            final_status = "cancelled"
            break
        elif status == "failed":
            final_status = "failed"
            final_error = error
            break

        sorted_nodes = await _run_reflection_and_replanning(
            execution_id=execution_id,
            node=node,
            idx=idx,
            sorted_nodes=sorted_nodes,
            edges=edges,
            ctx=ctx,
            logs=logs,
        )
        agent_count = len(sorted_nodes)

    # Finalise execution record
    completed_now = datetime.now(timezone.utc).isoformat()

    if final_status == "completed":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.COMPLETED,
            reason="Workflow completed successfully",
            metadata={"workflowName": workflow_name},
        )

        completion_log = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": completed_now,
            "level": "info",
            "agentName": "system",
            "content": f"✓ Workflow '{workflow_name}' completed successfully",
        }
        logs.append(completion_log)

        await _emit(
            execution_id,
            "log",
            "system",
            f"✓ Workflow '{workflow_name}' completed successfully",
            logId=completion_log["id"],
            level="info",
        )

        await _emit(
            execution_id,
            "workflow_completed",
            "system",
            f"✓ Workflow '{workflow_name}' completed successfully",
            executionId=execution_id,
            totalAgents=agent_count,
            duration=(
                datetime.now(timezone.utc) - datetime.fromisoformat(now)
            ).total_seconds(),
        )

        all_outputs = ctx.get_all_outputs()
        agent_outputs = []
        for s in steps:
            if s.get("output"):
                agent_outputs.append(
                    f"{s['agentName']}: {s['output'][:200]}"
                )
        summary_text = "\n".join(agent_outputs[:10])

        await db.execute(
            """UPDATE executions
               SET status = 'completed', completed_at = ?,
                   output = ?, summary = ?
               WHERE id = ?""",
            (
                completed_now,
                json.dumps(all_outputs),
                summary_text,
                execution_id,
            ),
        )
        await db.commit()

    elif final_status == "cancelled":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.CANCELLED,
            reason="Workflow execution cancelled",
            metadata={"workflowName": workflow_name},
        )

        await _emit(
            execution_id,
            "workflow_cancelled",
            "system",
            "Execution cancelled",
            executionId=execution_id,
        )

        await db.execute(
            """UPDATE executions
               SET status = 'cancelled', completed_at = ?, error = ?
               WHERE id = ?""",
            (completed_now, "Cancelled during execution", execution_id),
        )
        await db.commit()

    elif final_status == "failed":
        await _emit(
            execution_id,
            "workflow_failed",
            "system",
            f"✕ Workflow failed: {final_error}",
            executionId=execution_id,
            error=final_error,
        )

        error_summary = (
            f"Failed at step: {steps[-1]['agentName'] if steps else 'unknown'}"
            f" — {final_error[:300]}"
        )
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.FAILED,
            reason="Workflow execution failed",
            metadata={"workflowName": workflow_name},
        )

        await db.execute(
            """UPDATE executions
               SET status = 'failed', completed_at = ?,
                   error = ?, summary = ?
               WHERE id = ?""",
            (completed_now, final_error, error_summary, execution_id),
        )
        await db.commit()

    # Send sentinel to end the stream
    await _end_stream(execution_id)

    logger.info(
        "Execution %s: status=%s | %d steps | %d logs",
        execution_id,
        final_status,
        len(steps),
        len(logs),
    )

    return {
        "executionId": execution_id,
        "status": final_status,
        "steps": steps,
        "logs": logs,
        "error": final_error,
    }


# ── Resume after approval ────────────────────────────────────────


async def resume_workflow(
    execution_id: str,
    agent_timeout: int = _DEFAULT_AGENT_TIMEOUT,
) -> dict[str, Any]:
    """
    Resume execution after an approval gate.
    Called when the user approves via POST /api/executions/{id}/approve.

    Loads the checkpoint and continues execution from the node
    AFTER the approval gate. Uses the shared _execute_agent_step
    to avoid code duplication with execute_workflow.
    """
    checkpoint = await _load_checkpoint(execution_id)
    if checkpoint is None:
        raise ValueError(
            f"No checkpoint found for execution {execution_id}"
        )

    sorted_nodes = checkpoint["nodes"]
    current_idx = checkpoint["current_idx"]
    workflow_id = checkpoint["workflow_id"]
    workflow_name = checkpoint["workflow_name"]
    workflow_description = checkpoint["workflow_description"]
    edges = checkpoint.get("edges", [])
    logs: list[dict[str, Any]] = checkpoint.get("logs", [])
    steps: list[dict[str, Any]] = checkpoint.get("steps", [])

    # Recreate context from checkpoint
    ctx = ContextManager(execution_id=execution_id)
    if checkpoint.get("context_state"):
        ctx.restore_state(checkpoint["context_state"])
    else:
        ctx = ContextManager(execution_id=execution_id)
        if checkpoint.get("context_initial"):
            ctx.set_context(checkpoint["context_initial"])
        for node_id, output_text in checkpoint.get(
            "context_outputs", {}
        ).items():
            parts = output_text.split("]: ", 1)
            agent_name = (
                parts[0].lstrip("[") if len(parts) > 1 else "Agent"
            )
            ctx.add_output(
                node_id,
                agent_name,
                parts[1] if len(parts) > 1 else output_text,
            )

    agent_count = len(sorted_nodes)
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()

    # Recreate stream
    await event_manager.create_stream(execution_id)

    # Update status to running
    await db.execute(
        "UPDATE executions SET status = 'running' WHERE id = ?",
        (execution_id,),
    )

    # Update approval record
    await db.execute(
        """UPDATE execution_approvals
           SET status = 'approved', decided_at = ?, decided_by = 'user'
           WHERE execution_id = ? AND status = 'pending'""",
        (now, execution_id),
    )
    await db.commit()

    await _record_runtime_state(
        execution_id=execution_id,
        to_state=RuntimeState.RUNNING,
        reason="Execution resumed after approval",
        metadata={
            "currentIdx": current_idx,
            "approvalNodeId": sorted_nodes[current_idx - 1].get("id"),
        },
    )

    # Emit: approval_granted
    approval_node_data = sorted_nodes[current_idx - 1].get("data", {})
    approval_name = approval_node_data.get("label", "Approval Gate")

    await _emit(
        execution_id,
        "approval_granted",
        approval_name,
        f'✅ Approval granted for "{approval_name}" — resuming execution',
        nodeId=sorted_nodes[current_idx - 1].get("id"),
    )

    ctx.add_approval_decision(
        node_id=sorted_nodes[current_idx - 1].get("id"),
        decision="approved",
        notes=f"Resumed after approval for {approval_name}",
    )
    await ctx.persist()

    grant_log = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": now,
        "level": "approval",
        "agentName": "system",
        "content": "✅ Approval granted — resuming execution",
    }
    logs.append(grant_log)

    await _emit(
        execution_id,
        "log",
        "system",
        "✅ Approval granted — resuming execution",
        logId=grant_log["id"],
        level="approval",
    )

    await _emit(
        execution_id,
        "execution_resumed",
        "system",
        "▶️ Execution resumed after approval",
        executionId=execution_id,
    )

    final_status = "completed"
    final_error: str | None = None

    # Continue execution from next node
    for idx, node in enumerate(sorted_nodes, start=1):
        if idx <= current_idx:
            continue

        node_data = node.get("data", {})
        agent_name = node_data.get(
            "label", node_data.get("role", f"Node {idx}")
        )

        # Check cancellation before each node
        if await _check_cancelled(execution_id, agent_name):
            final_status = "cancelled"
            break

        # Check if next node is also an approval gate
        if _is_approval_node(node):
            await _handle_approval_node(
                execution_id=execution_id,
                node=node,
                idx=idx,
                sorted_nodes=sorted_nodes,
                ctx=ctx,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                workflow_description=workflow_description,
                logs=logs,
                steps=steps,
                edges=edges,
                agent_count=agent_count,
            )
            await _end_stream(execution_id)
            return {
                "executionId": execution_id,
                "status": "waiting_approval",
                "steps": steps,
                "logs": logs,
                "error": None,
            }

        # Execute agent step using shared logic
        status, error = await _execute_agent_step(
            execution_id=execution_id,
            node=node,
            idx=idx,
            agent_count=agent_count,
            ctx=ctx,
            logs=logs,
            steps=steps,
            agent_timeout=agent_timeout,
        )

        if status == "cancelled":
            final_status = "cancelled"
            break
        elif status == "failed":
            final_status = "failed"
            final_error = error
            break

        sorted_nodes = await _run_reflection_and_replanning(
            execution_id=execution_id,
            node=node,
            idx=idx,
            sorted_nodes=sorted_nodes,
            edges=edges,
            ctx=ctx,
            logs=logs,
        )
        agent_count = len(sorted_nodes)

    # Finalise
    completed_now = datetime.now(timezone.utc).isoformat()

    if final_status == "completed":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.COMPLETED,
            reason="Workflow completed successfully",
            metadata={"workflowName": workflow_name},
        )

        await _emit(
            execution_id,
            "workflow_completed",
            "system",
            f"✓ Workflow '{workflow_name}' completed successfully",
            executionId=execution_id,
            totalAgents=agent_count,
            duration=0,
        )

        all_outputs = ctx.get_all_outputs()
        agent_outputs = []
        for s in steps:
            if s.get("output"):
                agent_outputs.append(
                    f"{s['agentName']}: {s['output'][:200]}"
                )
        summary_text = "\n".join(agent_outputs[:10])

        await db.execute(
            """UPDATE executions
               SET status = 'completed', completed_at = ?,
                   output = ?, summary = ?
               WHERE id = ?""",
            (
                completed_now,
                json.dumps(all_outputs),
                summary_text,
                execution_id,
            ),
        )
        await db.commit()

    elif final_status == "cancelled":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.CANCELLED,
            reason="Workflow execution cancelled",
            metadata={"workflowName": workflow_name},
        )

        await _emit(
            execution_id,
            "workflow_cancelled",
            "system",
            "Execution cancelled",
            executionId=execution_id,
        )

        await db.execute(
            """UPDATE executions
               SET status = 'cancelled', completed_at = ?, error = ?
               WHERE id = ?""",
            (completed_now, "Cancelled during execution", execution_id),
        )
        await db.commit()

    elif final_status == "failed":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.FAILED,
            reason="Workflow execution failed",
            metadata={"workflowName": workflow_name},
        )

        await _emit(
            execution_id,
            "workflow_failed",
            "system",
            f"✕ Workflow failed: {final_error}",
            executionId=execution_id,
            error=final_error,
        )

        await db.execute(
            """UPDATE executions
               SET status = 'failed', completed_at = ?, error = ?
               WHERE id = ?""",
            (completed_now, final_error, execution_id),
        )
        await db.commit()

    await _end_stream(execution_id)

    logger.info(
        "Resumed execution %s: status=%s | %d steps",
        execution_id,
        final_status,
        len(steps),
    )

    return {
        "executionId": execution_id,
        "status": final_status,
        "steps": steps,
        "logs": logs,
        "error": final_error,
    }


# ── Reject execution ─────────────────────────────────────────────


async def reject_execution(execution_id: str) -> dict[str, Any]:
    """
    Reject a paused execution.
    Called when the user rejects via POST /api/executions/{id}/reject.

    Marks the execution as rejected, updates approval record,
    and emits final rejection events.
    """
    checkpoint = await _load_checkpoint(execution_id)
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()

    # Recreate stream for final events
    await event_manager.create_stream(execution_id)

    # Update status to rejected
    await db.execute(
        "UPDATE executions SET status = 'rejected', completed_at = ? WHERE id = ?",
        (now, execution_id),
    )

    # Update approval record
    await db.execute(
        """UPDATE execution_approvals
           SET status = 'rejected', decided_at = ?, decided_by = 'user'
           WHERE execution_id = ? AND status = 'pending'""",
        (now, execution_id),
    )
    await db.commit()

    await _record_runtime_state(
        execution_id=execution_id,
        to_state=RuntimeState.REJECTED,
        reason="Execution rejected by user",
        metadata={"rejectedAt": now},
    )

    # Persist rejection into shared memory if available
    if checkpoint:
        try:
            ctx = await ContextManager.load_from_db(execution_id)
            approval_node = checkpoint.get("nodes", [])[checkpoint.get("current_idx", 1) - 1]
            ctx.add_approval_decision(
                node_id=approval_node.get("id", "unknown"),
                decision="rejected",
                notes="Execution rejected by user",
            )
            await ctx.persist()
        except Exception:
            logger.debug("Unable to persist approval rejection memory for %s", execution_id)

    # Get approval name from checkpoint
    if checkpoint:
        sorted_nodes = checkpoint.get("nodes", [])
        current_idx = checkpoint.get("current_idx", 1)
        approval_node = (
            sorted_nodes[current_idx - 1]
            if current_idx <= len(sorted_nodes)
            else {}
        )
        approval_name = approval_node.get("data", {}).get(
            "label", "Approval Gate"
        )
    else:
        approval_name = "Approval Gate"

    # Emit: approval_rejected
    await _emit(
        execution_id,
        "approval_rejected",
        approval_name,
        f'❌ Approval rejected for "{approval_name}" — execution cancelled',
    )

    await _emit(
        execution_id,
        "log",
        "system",
        f'❌ Approval rejected — execution cancelled at "{approval_name}"',
        level="approval",
    )

    # Also emit workflow_failed with a clear message
    await _emit(
        execution_id,
        "workflow_failed",
        "system",
        f'Execution rejected at approval gate: "{approval_name}"',
        executionId=execution_id,
        reason="rejected",
    )

    # End stream
    await _end_stream(execution_id)

    logger.info(
        "Execution %s rejected at approval gate '%s'",
        execution_id,
        approval_name,
    )

    return {
        "executionId": execution_id,
        "status": "rejected",
        "steps": [],
        "logs": [],
        "error": f"Rejected at approval gate: {approval_name}",
    }
