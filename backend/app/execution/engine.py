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
import re
import traceback
import uuid
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any

from crewai import Task as CrewTask

from app.database.database import get_db
from app.execution.agent_factory import build_agent, build_agent_routed
from app.execution.production_safety import ProductionSafetyGuard
from app.execution.execution_analytics import ExecutionAnalytics
from app.execution.memory_lifecycle import MemoryLifecycleManager
from app.execution.context_manager import ContextManager
from app.execution.final_synthesizer import FinalResponseSynthesizer
from app.execution.manager import execution_manager
from app.execution.execution_stability import ExecutionStabilityMonitor
from app.execution.runtime_performance import RuntimePerformanceTracker
from app.execution.runtime_hardening import RuntimeHardening
from app.execution.adaptive_graph import AdaptiveExecutionGraph
from app.execution.confidence_engine import ConfidenceEngine
from app.execution.dynamic_planner import DynamicPlanner
from app.execution.hierarchical_memory import HierarchicalMemory
from app.execution.memory_runtime import MemoryRuntime
from app.execution.parallel_runtime import ParallelRuntime
from app.execution.research_agents import is_research_objective
from app.config.locale import language_instruction, t
from app.execution.research_pipeline_state import (
    is_research_pipeline_ready,
    pipeline_synthesis_mode_block,
    research_pipeline_context_block,
)
from app.execution.tool_planning import ToolPlanner
from app.tools.tool_orchestrator import ToolOrchestrator
from app.execution.execution_runtime import ExecutionRuntime
from app.execution.runtime_lifecycle import ExecutionPhase, LifecycleManager
from app.execution.runtime_state import RuntimeState
from app.tools.tool_invocation import (
    MAX_TOOL_INVOCATIONS,
    ToolInvocationOutcome,
    execute_tool_request_if_present,
    parse_tool_request,
)
from app.tools.tool_models import ToolCallRequest
from app.tools.tool_registry import tool_registry
from app.tools.tool_safety import is_auto_approved, requires_manual_approval
from app.streaming.event_manager import event_manager, StreamEvent
from app.runtime.execution_kernel import ExecutionKernel
from app.runtime.action_system import (
    ActionContext,
    ActionPriority,
    ActionType,
    RuntimeAction,
)

logger = logging.getLogger(__name__)

# Default per-agent timeout (seconds).
# qwen2.5:3b on CPU with large accumulated context can take 8-10 min per step.
_DEFAULT_AGENT_TIMEOUT = 600  # 10 minutes


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


def _format_tool_descriptions() -> str:
    """Return a short list of available tools for the prompt."""
    lines: list[str] = ["Available tools:"]
    for tool in tool_registry.list():
        if is_auto_approved(tool.name):
            policy = "auto-approve"
        elif requires_manual_approval(tool.name):
            policy = "requires approval"
        else:
            policy = tool.permission_level
        lines.append(f"- {tool.name}: {tool.description} [{policy}]")
    return "\n".join(lines)


def _tool_usage_instructions(*, skip_web_search: bool = False) -> str:
    research_flow = (
        "Preferred research flow:\n"
        "  1. web_search with your query to find sources\n"
        "  2. webpage_fetch on relevant URLs to read content\n"
        "  3. structured_data_extractor or markdown_generator to format findings\n\n"
    )
    if skip_web_search:
        research_flow = (
            "Pre-collected research evidence is already in context above.\n"
            "Synthesize from that evidence. Only call tools if a specific claim still needs verification.\n\n"
        )
    return (
        "IMPORTANT: For factual research, current events, or external data you MUST use tools "
        "instead of guessing or hallucinating.\n"
        f"{research_flow}"
        "To invoke a tool, respond with ONLY a single JSON object:\n"
        '{"tool_name": "<tool_name>", "input_data": { ... }}\n'
        "Do not include markdown or explanation with the tool request.\n"
        "After the tool runs, its output appears in shared context — continue reasoning from that data.\n"
        "When evidence is sufficient, respond with prose (NOT JSON) as your final answer.\n"
        "Safe tools (calculator, web_search, webpage_fetch, file_reader, etc.) run immediately.\n"
        "Restricted tools (shell_command, file_write, file_delete, http_post) pause for approval.\n"
    )


def _final_answer_instructions(*, strict: bool = False) -> str:
    base = (
        "--- FINAL STEP ---\n"
        "Tools have already run and results are in context above.\n"
        "Write your complete final answer in clear markdown prose with headings "
        "(Summary, Findings, Recommendations, Next Steps).\n"
        "Do NOT output JSON, tool requests, or {\"tool_name\": ...} payloads.\n"
    )
    if strict:
        base += (
            "Your previous response was rejected because it was not valid prose. "
            "Output markdown only — no code blocks containing JSON.\n"
        )
    return base


def _pipeline_synthesis_mode_block() -> str:
    return pipeline_synthesis_mode_block()


def _research_pipeline_ready(ctx: ContextManager) -> bool:
    return is_research_pipeline_ready(ctx)


def _research_pipeline_block(ctx: ContextManager) -> str:
    return research_pipeline_context_block(ctx)


def _fallback_research_prose(ctx: ContextManager, goal: str) -> str:
    """Deterministic prose when the model keeps returning tool JSON."""
    summary = ctx.get_workflow_memory("research_pipeline_summary") or {}
    block = str(ctx.get_workflow_memory("research_context_block") or "").strip()
    return "\n".join([
        "## Summary",
        str(summary.get("summary") or "Research pipeline collected evidence for this objective."),
        "",
        "## Findings",
        block[:6000] if block else "See pre-collected source material in execution context.",
        "",
        "## Recommendations",
        "Validate financial projections against primary sources before acting on this research.",
        "",
        "## Next Steps",
        goal[:300] or "Review cited sources and monitor NVIDIA Blackwell adoption metrics.",
    ])


def _needs_prose_finalization(output_text: str, pipeline_ready: bool) -> bool:
    if parse_tool_request(output_text) is not None:
        return True
    if not pipeline_ready:
        return False
    words = re.findall(r"\w+", output_text or "")
    return len(words) < 50


async def _finalize_agent_output(
    *,
    execution_id: str,
    agent: Any,
    node_data: dict[str, Any],
    output_text: str,
    combined_context: str,
    tool_plan_block: str,
    research_block: str,
    agent_timeout: int,
    pipeline_ready: bool,
    node_id: str,
    agent_name: str,
    ctx: ContextManager,
) -> str:
    if not _needs_prose_finalization(output_text, pipeline_ready):
        return output_text

    for attempt in range(2):
        synthesis_prompt = (
            _resolve_prompt(
                node_data, combined_context, tool_plan_block, research_block
            )
            + "\n\n"
            + _pipeline_synthesis_mode_block()
            + "\n"
            + _final_answer_instructions(strict=attempt > 0)
        )
        task = _build_execution_task(agent, synthesis_prompt)
        await ExecutionStabilityMonitor.start_agent_monitor(
            execution_id=execution_id,
            node_id=node_id,
            agent_name=agent_name,
            emit_fn=_emit,
            cancel_check=execution_manager.is_cancellation_requested,
        )
        try:
            output_text = await _run_agent_with_timeout(
                execution_id=execution_id,
                agent=agent,
                task=task,
                timeout=agent_timeout,
            )
        finally:
            await ExecutionStabilityMonitor.stop_agent_monitor(execution_id)

        if not _needs_prose_finalization(output_text, pipeline_ready):
            return output_text

    goal = node_data.get("goal", "")
    if pipeline_ready:
        return _fallback_research_prose(ctx, goal)
    return output_text


def _resolve_prompt(
    node_data: dict[str, Any],
    context: str,
    tool_plan_block: str = "",
    research_block: str = "",
) -> str:
    """Build the task prompt for an agent node."""
    goal = node_data.get("goal", "Complete the assigned task.")
    label = node_data.get("label", "Agent")

    parts = [t("acting_as", label=label), "", t("your_goal", goal=goal)]

    if research_block.strip():
        parts.extend(["", research_block])

    if context.strip():
        parts.extend(["", t("context_from_agents"), context])

    if tool_plan_block.strip():
        parts.extend(["", tool_plan_block])

    skip_web_search = bool(research_block.strip())
    if skip_web_search:
        parts.extend([
            "",
            t("instructions"),
            _pipeline_synthesis_mode_block(),
            t("complete_goal_with_evidence"),
            t("be_concise"),
            t("respond_markdown_sections"),
            language_instruction(),
        ])
    else:
        parts.extend([
            "",
            t("instructions"),
            t("complete_goal_with_context"),
            t("be_concise"),
            t("use_tools_not_guess"),
            "",
            t("tool_guidance"),
            _tool_usage_instructions(skip_web_search=False),
            "",
            _format_tool_descriptions(),
            language_instruction(),
        ])

    return "\n".join(parts)


def _format_tool_request_summary(request: ToolCallRequest) -> str:
    return (
        f"Tool request: {request.tool_name} with input {json.dumps(request.input_data, ensure_ascii=False)}"
    )


async def _maybe_execute_agent_tool_call(
    execution_id: str,
    node_id: str,
    agent_name: str,
    output_text: str,
    ctx: ContextManager,
) -> ToolInvocationOutcome | None:
    outcome = await execute_tool_request_if_present(
        execution_id=execution_id,
        node_id=node_id,
        agent_name=agent_name,
        raw_text=output_text,
        ctx=ctx,
    )

    if outcome is None:
        return None

    request = outcome.request
    response = outcome.response

    await _emit(
        execution_id,
        "tool_requested",
        agent_name,
        _format_tool_request_summary(request),
        toolName=request.tool_name,
        nodeId=node_id,
    )

    if response.status == "completed":
        await _emit(
            execution_id,
            "tool_completed",
            agent_name,
            t("tool_completed", name=response.tool_name),
            toolName=response.tool_name,
            toolOutput=response.output,
        )
        # Frontend-compatible aliases
        await _emit(
            execution_id,
            "tool_invoked",
            agent_name,
            f"Invoked {response.tool_name}",
            toolName=response.tool_name,
            input=request.input_data,
            data={
                "tool_name": response.tool_name,
                "input": request.input_data,
                "invocation_id": f"{node_id}-{response.tool_name}",
            },
        )
        await _emit(
            execution_id,
            "tool_result",
            agent_name,
            f"Tool {response.tool_name} result ready",
            toolName=response.tool_name,
            toolOutput=response.output,
            data={
                "tool_name": response.tool_name,
                "output": response.output,
                "status": "completed",
                "invocation_id": f"{node_id}-{response.tool_name}",
            },
        )
        return outcome

    if response.status == "pending_approval":
        await _emit(
            execution_id,
            "tool_approval_requested",
            agent_name,
            f"Tool {response.tool_name} requires approval before execution.",
            toolName=response.tool_name,
            toolError=response.error,
        )
        return outcome

    await _emit(
        execution_id,
        "tool_failed",
        agent_name,
        f"Tool {response.tool_name} failed: {response.error}",
        toolName=response.tool_name,
        toolError=response.error,
    )
    return outcome


# ── Streaming event helpers ───────────────────────────────────────────


def _build_execution_task(agent: Any, prompt: str) -> Any:
    """
    Build a task object compatible with the underlying agent implementation.

    CrewAI agents require CrewTask validation; provider adapters only need a
    description field.
    """
    if agent.__class__.__module__.startswith("crewai"):
        return CrewTask(
            description=prompt,
            expected_output="A concise, well-structured response addressing the goal.",
            agent=agent,
        )
    return SimpleNamespace(description=prompt)


async def _emit(
    execution_id: str,
    event_type: str,
    agent: str,
    message: str,
    **data: Any,
) -> None:
    """Emit a typed event via the unified RuntimeEventBus."""
    runtime = ExecutionRuntime.get(execution_id)
    if runtime is not None:
        await runtime.emit(event_type, agent, message, **data)
        return

    # Fallback for executions without a bound runtime (e.g. resume edge cases)
    if execution_manager.is_cancellation_requested(execution_id):
        return
    event = StreamEvent(type=event_type, agent=agent, message=message, data=data)
    await event_manager.emit(execution_id, event)


async def _end_stream(execution_id: str) -> None:
    """Send sentinel to end the SSE stream."""
    runtime = ExecutionRuntime.get(execution_id)
    if runtime is not None:
        await runtime.end_stream()
        return
    if not execution_manager.is_cancellation_requested(execution_id):
        await event_manager.emit(execution_id, None)


async def _record_runtime_state(
    execution_id: str,
    to_state: RuntimeState,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    from_state: RuntimeState | str | None = None,
    phase: ExecutionPhase | None = None,
) -> None:
    """Persist a runtime state transition via LifecycleManager."""
    runtime = ExecutionRuntime.get(execution_id)
    if runtime is not None:
        await runtime.lifecycle.transition(
            to_state=to_state,
            reason=reason,
            metadata=metadata,
            from_state=from_state,
            phase=phase,
        )
        return

    lifecycle = LifecycleManager(execution_id)
    await lifecycle.transition(
        to_state=to_state,
        reason=reason,
        metadata=metadata,
        from_state=from_state,
        phase=phase,
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
        t("activating_agent", idx=idx, total=agent_count, name=agent_name),
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
        "content": t("activating_agent", idx=idx, total=agent_count, name=agent_name),
    }
    logs.append(log_entry)

    log_entry2 = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "agent",
        "agentName": agent_name,
        "content": t("starting_task", goal=node_data.get("goal", t("no_goal"))),
    }
    logs.append(log_entry2)

    await _emit(
        execution_id,
        "log",
        agent_name,
        t("starting_task", goal=node_data.get("goal", t("no_goal"))),
        logId=log_entry2["id"],
        level="agent",
    )

    try:
        # Production safety: duration + retry circuit
        safety = ProductionSafetyGuard.mid_execution_check(execution_id)
        if not safety.allowed:
            return "failed", safety.reason

        # Build task with role-based context budget
        task_complexity = str(
            ctx.get_workflow_memory("task_complexity") or "moderate"
        )
        is_synthesis = (
            "synthes" in agent_name.lower()
            or "synthes" in node_data.get("role", "").lower()
        )
        is_retry = bool(
            node_data.get("executionMetadata", {}).get("recovery")
            or agent_name.lower().startswith("retry ")
        )
        model_tier = (
            "synthesis" if is_synthesis
            else "retry" if is_retry
            else "lightweight" if task_complexity in ("simple", "low")
            else "reasoning" if task_complexity in ("complex", "high")
            else "standard"
        )

        tool_plan = ToolPlanner.infer_tools_for_node(node)
        ToolPlanner.persist_plan(ctx, tool_plan)
        tool_plan_block = ToolPlanner.build_tool_context_block(tool_plan)
        pipeline_ready = _research_pipeline_ready(ctx)
        research_block = _research_pipeline_block(ctx)
        if ConfidenceEngine.should_force_tool_grounding(ctx):
            tool_plan_block += (
                "\n\nLOW CONFIDENCE: Use web_search and webpage_fetch to ground "
                "factual claims before concluding.\n"
            )
            ctx.set_workflow_memory("pending_confidence_action", None)

        memory_runtime = MemoryRuntime.for_execution(execution_id, ctx)
        assembly = await memory_runtime.assemble_context(
            model_tier=model_tier,
            is_synthesis=is_synthesis,
            is_retry=is_retry,
            agent_name=agent_name,
            agent_role=node_data.get("role", ""),
            emit_fn=_emit,
        )
        combined_context = assembly.context
        budget_meta = assembly.metadata
        await _emit(
            execution_id,
            "context_budget",
            agent_name,
            t("context_budget", tokens=budget_meta.get("estimatedTokens", 0)),
            nodeId=node_id,
            **{k: v for k, v in budget_meta.items() if isinstance(v, (str, int, float, bool, list))},
        )

        agent, _selected_model = await build_agent_routed(
            node_data,
            execution_id=execution_id,
            node_id=node_id,
            agent_name=agent_name,
            task_complexity=task_complexity,
            context_text=combined_context,
            needs_tools=bool(tool_plan.recommended_tools),
            is_synthesis=is_synthesis,
            node_index=idx,
            total_nodes=agent_count,
            emit_fn=_emit,
        )
        prompt = _resolve_prompt(
            node_data, combined_context, tool_plan_block, research_block
        )

        task = _build_execution_task(agent, prompt)

        # Emit: thinking
        await _emit(
            execution_id,
            "thinking",
            agent_name,
            t("agent_thinking", name=agent_name),
            nodeId=node_id,
            plannedTools=tool_plan.recommended_tools,
        )

        await RuntimeHardening.emit_memory_warning(execution_id, _emit)
        RuntimePerformanceTracker.begin_stage(execution_id, "agent", agent_name)

        # Heartbeat monitor for long-running local LLM agents
        await ExecutionStabilityMonitor.start_agent_monitor(
            execution_id=execution_id,
            node_id=node_id,
            agent_name=agent_name,
            emit_fn=_emit,
            cancel_check=execution_manager.is_cancellation_requested,
        )

        try:
            output_text = await _run_agent_with_timeout(
                execution_id=execution_id,
                agent=agent,
                task=task,
                timeout=agent_timeout,
            )
        finally:
            await ExecutionStabilityMonitor.stop_agent_monitor(execution_id)

        RuntimePerformanceTracker.end_stage(execution_id, "agent", agent_name)

        # When pipeline evidence exists, skip agent tool loops entirely.
        if not pipeline_ready:
            last_tool_key: tuple[str, str] | None = None
            for attempt in range(MAX_TOOL_INVOCATIONS):
                outcome = await _maybe_execute_agent_tool_call(
                    execution_id=execution_id,
                    node_id=node_id,
                    agent_name=agent_name,
                    output_text=output_text,
                    ctx=ctx,
                )

                if outcome is None:
                    break

                if outcome.response.status == "completed":
                    tool_key = (
                        outcome.request.tool_name,
                        json.dumps(
                            outcome.request.input_data,
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                    )
                    if tool_key == last_tool_key:
                        break
                    last_tool_key = tool_key

                    ctx.set_workflow_memory(
                        "last_tool_result",
                        {
                            "tool_name": outcome.response.tool_name,
                            "output": outcome.response.output,
                        },
                    )
                    await ctx.persist()

                    memory_runtime = MemoryRuntime.for_execution(execution_id, ctx)
                    assembly = await memory_runtime.assemble_context(
                        model_tier=model_tier,
                        is_synthesis=is_synthesis,
                        is_retry=is_retry,
                        agent_name=agent_name,
                        agent_role=node_data.get("role", ""),
                        emit_fn=_emit,
                    )
                    combined_context = assembly.context
                    prompt = _resolve_prompt(
                        node_data, combined_context, tool_plan_block, research_block
                    )
                    task = _build_execution_task(agent, prompt)
                    await ExecutionStabilityMonitor.start_agent_monitor(
                        execution_id=execution_id,
                        node_id=node_id,
                        agent_name=agent_name,
                        emit_fn=_emit,
                        cancel_check=execution_manager.is_cancellation_requested,
                    )
                    try:
                        output_text = await _run_agent_with_timeout(
                            execution_id=execution_id,
                            agent=agent,
                            task=task,
                            timeout=agent_timeout,
                        )
                    finally:
                        await ExecutionStabilityMonitor.stop_agent_monitor(execution_id)
                    continue

                if outcome.response.status == "pending_approval":
                    failed_now = datetime.now(timezone.utc).isoformat()
                    await db.execute(
                        """UPDATE execution_steps
                           SET status = 'failed', output = ?, completed_at = ?
                           WHERE id = ?""",
                        (
                            f"Tool approval required for {outcome.response.tool_name}",
                            failed_now,
                            step_id,
                        ),
                    )
                    await db.commit()
                    return "failed", outcome.response.error or "Tool approval required"

                if outcome.response.status == "failed":
                    failed_now = datetime.now(timezone.utc).isoformat()
                    await db.execute(
                        """UPDATE execution_steps
                           SET status = 'failed', output = ?, completed_at = ?
                           WHERE id = ?""",
                        (outcome.response.error or "Tool failed", failed_now, step_id),
                    )
                    await db.commit()
                    return "failed", outcome.response.error or "Tool failed"

        output_text = await _finalize_agent_output(
            execution_id=execution_id,
            agent=agent,
            node_data=node_data,
            output_text=output_text,
            combined_context=combined_context,
            tool_plan_block=tool_plan_block,
            research_block=research_block,
            agent_timeout=agent_timeout,
            pipeline_ready=pipeline_ready,
            node_id=node_id,
            agent_name=agent_name,
            ctx=ctx,
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

        # Emit: output (single event — no duplicate log stream)
        await _emit(
            execution_id,
            "output",
            agent_name,
            output_text[:500],
            nodeId=node_id,
            output=output_text,
            truncated=len(output_text) > 500,
        )

        logs.append({
            "id": uuid.uuid4().hex[:12],
            "timestamp": completed_now,
            "level": "output",
            "agentName": agent_name,
            "content": output_text[:500],
        })

        # Emit: agent_completed
        await _emit(
            execution_id,
            "agent_completed",
            agent_name,
            t("agent_completed", name=agent_name),
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
            "content": t("agent_completed", name=agent_name),
        }
        logs.append(log_entry5)

        await _emit(
            execution_id,
            "log",
            "system",
            t("agent_completed", name=agent_name),
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


async def _emit_confidence(
    execution_id: str,
    node: dict[str, Any],
    ctx: ContextManager,
) -> None:
    assessment = ConfidenceEngine.assess(node, ctx)
    await _emit(
        execution_id,
        "confidence_updated",
        assessment.agent_name,
        t("confidence", score=f"{assessment.score:.0%}", reasoning=assessment.reasoning),
        nodeId=assessment.node_id,
        confidenceScore=assessment.score,
        triggers=assessment.triggers,
        recommendedAction=assessment.recommended_action.value,
        factuality=assessment.factuality,
        completion=assessment.completion,
    )
    memory = HierarchicalMemory(ctx)
    memory.record_execution(
        assessment.node_id,
        assessment.agent_name,
        ctx.get_all_outputs().get(assessment.node_id, ""),
        confidence=assessment.score,
    )
    output = ctx.get_all_outputs().get(assessment.node_id, "")
    if output:
        memory.summarize_to_long_term(assessment.agent_name, output)
    memory.sync_from_context()
    # Sync through unified coordinator when runtime is active
    mem_rt = MemoryRuntime.get(execution_id)
    if mem_rt:
        mem_rt.coordinator.sync_hierarchical()


async def _run_reflection_and_replanning(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    sorted_nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ctx: ContextManager,
    logs: list[dict[str, Any]],
    graph: AdaptiveExecutionGraph | None = None,
) -> list[dict[str, Any]]:
    """Evaluate output quality via ReflectionCoordinator and replan if needed."""
    kernel = ExecutionKernel.get(execution_id)
    if kernel is not None and graph is not None:
        kernel.set_dispatcher_extra(
            graph=graph,
            sorted_nodes=sorted_nodes,
            edges=edges,
            logs=logs,
        )
        action = RuntimeAction(
            action_type=ActionType.REFLECTION,
            context=ActionContext(
                execution_id=execution_id,
                node_id=node.get("id", ""),
                agent_name=node.get("data", {}).get("label", "agent"),
            ),
            payload={"node": node, "idx": idx},
            priority=ActionPriority.NORMAL,
        )
        result = await kernel.dispatch_action(action)
        if result.success and result.output is not None:
            return result.output
        return sorted_nodes

    runtime = ExecutionRuntime.get(execution_id)
    if runtime is None:
        runtime = ExecutionRuntime(execution_id, ctx)
    else:
        runtime.bind_context(ctx)

    runtime.telemetry.record_reflection()
    result = await runtime.reflection.evaluate_and_emit(
        node=node,
        emit_fn=_emit,
        graph=graph,
        idx=idx,
        sorted_nodes=sorted_nodes,
        edges=edges,
        logs=logs,
    )
    return result if result is not None else sorted_nodes


async def _process_completed_node(
    execution_id: str,
    node: dict[str, Any],
    idx: int,
    sorted_nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    graph: AdaptiveExecutionGraph,
    ctx: ContextManager,
    logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Post-step: confidence, reflection, dynamic subgoals."""
    graph.mark_completed(node.get("id", ""))
    await _emit_confidence(execution_id, node, ctx)
    output = ctx.get_all_outputs().get(node.get("id", ""), "")
    spawned = await DynamicPlanner.maybe_spawn_subgoal_agents(
        execution_id, graph, node, output, ctx, _emit
    )
    if spawned:
        sorted_nodes = graph.ordered_nodes()
    sorted_nodes = await _run_reflection_and_replanning(
        execution_id=execution_id,
        node=node,
        idx=idx,
        sorted_nodes=sorted_nodes,
        edges=edges,
        ctx=ctx,
        logs=logs,
        graph=graph,
    )
    return sorted_nodes


async def _execute_nodes_adaptive(
    execution_id: str,
    workflow_id: str,
    workflow_name: str,
    workflow_description: str,
    sorted_nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    ctx: ContextManager,
    logs: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    agent_timeout: int,
    start_after_idx: int = 0,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """
    Adaptive DAG scheduler with parallel batches and runtime replanning.

    Returns (final_status, final_error, updated_sorted_nodes).
    """
    graph, _strategy = await DynamicPlanner.initialize_execution(
        execution_id=execution_id,
        objective=workflow_description,
        nodes=sorted_nodes,
        edges=edges,
        ctx=ctx,
        emit_fn=_emit,
        resume=start_after_idx > 0,
    )

    if start_after_idx == 0 and workflow_description.strip():
        try:
            from app.cognition.cognitive_orchestrator import CognitiveOrchestrator

            await CognitiveOrchestrator.run_pre_execution(
                execution_id=execution_id,
                objective=workflow_description,
                ctx=ctx,
                emit_fn=_emit,
            )
            await ctx.persist()
        except Exception as exc:
            logger.warning("Cognitive pre-execution failed for %s: %s", execution_id, exc)

    if start_after_idx == 0:
        from app.execution.coding_agents import (
            is_coding_objective,
            is_browser_objective,
            is_computer_use_pipeline_objective,
        )
        from app.computer_use.computer_use_agents import is_computer_workflow_objective

        kernel = ExecutionKernel.get(execution_id)
        if kernel is not None:
            kernel.set_dispatcher_extra(
                graph=graph,
                sorted_nodes=sorted_nodes,
                edges=edges,
                logs=logs,
            )
            try:
                await kernel.schedule_pipeline_actions(
                    workflow_description,
                    include_coding=is_coding_objective(workflow_description),
                    include_browser=is_browser_objective(workflow_description)
                    and not is_computer_use_pipeline_objective(workflow_description),
                    include_computer_use=is_computer_use_pipeline_objective(workflow_description)
                    and not is_computer_workflow_objective(workflow_description),
                    include_computer_workflow=is_computer_workflow_objective(workflow_description),
                    include_research=is_research_objective(workflow_description),
                )
                await kernel.run_scheduled_pipelines()
            except Exception as exc:
                logger.warning(
                    "Kernel pipeline dispatch failed for %s: %s",
                    execution_id,
                    exc,
                )
        else:
            from app.coding.coding_orchestrator import CodingOrchestrator
            from app.browser.browser_orchestrator import BrowserOrchestrator
            from app.computer_use.computer_use_orchestrator import ComputerUseOrchestrator

            if is_computer_use_pipeline_objective(workflow_description):
                if not ctx.get_workflow_memory("computer_use_pipeline_completed"):
                    try:
                        await ComputerUseOrchestrator.execute_computer_use_pipeline(
                            execution_id=execution_id,
                            objective=workflow_description,
                            ctx=ctx,
                            emit_fn=_emit,
                        )
                        ctx.set_workflow_memory("computer_use_pipeline_completed", True)
                        await ctx.persist()
                    except Exception as exc:
                        logger.warning(
                            "Computer use pipeline failed for %s: %s",
                            execution_id,
                            exc,
                        )

            if is_coding_objective(workflow_description):
                if not ctx.get_workflow_memory("coding_pipeline_completed"):
                    try:
                        await CodingOrchestrator.execute_coding_pipeline(
                            execution_id=execution_id,
                            objective=workflow_description,
                            ctx=ctx,
                            emit_fn=_emit,
                        )
                        ctx.set_workflow_memory("coding_pipeline_completed", True)
                        await ctx.persist()
                    except Exception as exc:
                        logger.warning(
                            "Autonomous coding pipeline failed for %s: %s",
                            execution_id,
                            exc,
                        )

            if is_browser_objective(workflow_description):
                if not ctx.get_workflow_memory("browser_pipeline_completed"):
                    if not ctx.get_workflow_memory("computer_use_pipeline_completed"):
                        try:
                            await BrowserOrchestrator.execute_browser_pipeline(
                                execution_id=execution_id,
                                objective=workflow_description,
                                ctx=ctx,
                                emit_fn=_emit,
                            )
                            ctx.set_workflow_memory("browser_pipeline_completed", True)
                            await ctx.persist()
                        except Exception as exc:
                            logger.warning(
                                "Browser automation pipeline failed for %s: %s",
                                execution_id,
                                exc,
                            )

            if is_research_objective(workflow_description):
                if not ctx.get_workflow_memory("research_pipeline_completed"):
                    try:
                        await ToolOrchestrator.execute_research_pipeline(
                            execution_id=execution_id,
                            query=workflow_description,
                            ctx=ctx,
                            emit_fn=_emit,
                        )
                        ctx.set_workflow_memory("research_pipeline_completed", True)
                        await ctx.persist()
                    except Exception as exc:
                        logger.warning(
                            "Autonomous research pipeline failed for %s: %s",
                            execution_id,
                            exc,
                        )

    step_lock = asyncio.Lock()
    sorted_nodes = graph.ordered_nodes()
    agent_count = len(sorted_nodes)
    final_status = "completed"
    final_error: str | None = None
    processed: set[str] = set()

    if start_after_idx > 0:
        for i, node in enumerate(sorted_nodes):
            if i < start_after_idx:
                nid = node.get("id", "")
                if nid:
                    graph.mark_completed(nid)
                    processed.add(nid)

    while True:
        safety = ProductionSafetyGuard.mid_execution_check(execution_id)
        if not safety.allowed:
            return "failed", safety.reason, graph.ordered_nodes()

        ready = graph.get_ready_nodes()
        ready = [n for n in ready if n.get("id") not in processed]
        if not ready:
            break

        approval_in_batch = [n for n in ready if _is_approval_node(n)]
        if approval_in_batch:
            node = approval_in_batch[0]
            idx = len(processed) + 1
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
            return "waiting_approval", None, sorted_nodes

        agent_ready = [n for n in ready if not _is_approval_node(n)]
        if not agent_ready:
            break

        batch_results = await ParallelRuntime.execute_ready_batch(
            execution_id=execution_id,
            graph=graph,
            ready_nodes=agent_ready,
            agent_count=agent_count,
            ctx=ctx,
            logs=logs,
            steps=steps,
            execute_step=_execute_agent_step,
            emit_fn=_emit,
            agent_timeout=agent_timeout,
            check_cancelled=_check_cancelled,
            step_lock=step_lock,
        )

        for node, status, error in batch_results:
            node_id = node.get("id", "")
            processed.add(node_id)
            idx = len(processed)

            if status == "cancelled":
                final_status = "cancelled"
                return final_status, final_error, graph.ordered_nodes()
            if status == "failed":
                final_status = "failed"
                final_error = error
                graph.mark_failed(node_id)
                return final_status, final_error, graph.ordered_nodes()

            sorted_nodes = await _process_completed_node(
                execution_id=execution_id,
                node=node,
                idx=idx,
                sorted_nodes=sorted_nodes,
                edges=edges,
                graph=graph,
                ctx=ctx,
                logs=logs,
            )
            sorted_nodes = graph.ordered_nodes()
            agent_count = len(sorted_nodes)

            await ExecutionStabilityMonitor.save_checkpoint_snapshot(
                execution_id=execution_id,
                ctx_state=ctx.get_state(),
                sorted_nodes=sorted_nodes,
                current_idx=idx,
                steps=steps,
                logs=logs,
                edges=edges,
                workflow_meta={
                    "workflowId": workflow_id,
                    "workflowName": workflow_name,
                },
            )

            kernel = ExecutionKernel.get(execution_id)
            if kernel is not None:
                await kernel.checkpoint(
                    "running",
                    graph_state={
                        "processed": list(processed),
                        "nodeCount": agent_count,
                    },
                    reason="node_completed",
                )

        if final_status != "completed":
            break

    return final_status, final_error, sorted_nodes


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

    # Create execution stream and consolidated runtime
    await event_manager.create_stream(execution_id)

    ProductionSafetyGuard.record_execution_start(execution_id)
    RuntimePerformanceTracker.start_execution(execution_id)

    try:
        from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

        await InfrastructureCoordinator.get_instance().on_execution_start(
            execution_id, workflow_id
        )
    except Exception as exc:
        logger.debug("Infrastructure start hook skipped: %s", exc)

    # Initialize context
    ctx = ContextManager(execution_id=execution_id)
    runtime = ExecutionRuntime(execution_id, ctx)
    kernel = ExecutionKernel.for_execution(
        execution_id, ctx, workflow_id=workflow_id
    )
    kernel.bind_runtime(runtime)
    await kernel.initialize(_emit)
    ctx.set_context(workflow_description)
    ctx.set_workflow_memory("node_count", agent_count)
    for n in sorted_nodes:
        tc = n.get("data", {}).get("taskComplexity")
        if tc:
            ctx.set_workflow_memory("task_complexity", tc)
            break
    tool_plans = ToolPlanner.plan_workflow_tools(sorted_nodes, workflow_description)
    ToolPlanner.persist_workflow_plans(ctx, tool_plans)
    await ctx.persist()

    runtime.lifecycle.set_phase(ExecutionPhase.PRE_EXECUTION)
    await runtime.lifecycle.initialize(
        workflow_id=workflow_id,
        initial_state=RuntimeState.RUNNING,
        reason="Execution started",
        metadata={
            "workflowName": workflow_name,
            "agentCount": agent_count,
        },
    )
    runtime.lifecycle.set_phase(ExecutionPhase.EXECUTING)

    logs: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    final_status = "completed"
    final_error: str | None = None

    # Emit: workflow_started
    await _emit(
        execution_id,
        "workflow_started",
        "system",
        t("starting_workflow", name=workflow_name, count=agent_count),
        workflowId=workflow_id,
        workflowName=workflow_name,
        agentCount=agent_count,
    )

    logs.append({
        "id": uuid.uuid4().hex[:12],
        "timestamp": now,
        "level": "info",
        "agentName": "system",
        "content": t("starting_workflow", name=workflow_name, count=agent_count),
    })

    # Adaptive DAG execution (parallel batches + runtime replanning)
    safety = ProductionSafetyGuard.mid_execution_check(execution_id)
    if not safety.allowed:
        final_status = "failed"
        final_error = safety.reason
    else:
        final_status, final_error, sorted_nodes = await _execute_nodes_adaptive(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            workflow_description=workflow_description,
            sorted_nodes=sorted_nodes,
            edges=edges,
            ctx=ctx,
            logs=logs,
            steps=steps,
            agent_timeout=agent_timeout,
        )
        agent_count = len(sorted_nodes)

        if final_status == "waiting_approval":
            runtime.lifecycle.set_phase(ExecutionPhase.WAITING_APPROVAL)
            return {
                "executionId": execution_id,
                "status": "waiting_approval",
                "steps": steps,
                "logs": logs,
                "error": None,
            }

    # Finalise execution record
    completed_now = datetime.now(timezone.utc).isoformat()

    if final_status == "completed":
        runtime.lifecycle.set_phase(ExecutionPhase.SYNTHESIZING)
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.COMPLETED,
            reason=t("workflow_completed_reason"),
            metadata={"workflowName": workflow_name},
            phase=ExecutionPhase.COMPLETED,
        )

        # ── Final response synthesis ─────────────────────────────
        synthesized_markdown = ""
        synthesized_summary = ""
        try:
            await _emit(
                execution_id,
                "synthesis_started",
                "system",
                t("synthesizing"),
            )
            RuntimePerformanceTracker.begin_stage(execution_id, "synthesis", "system")
            synthesis = FinalResponseSynthesizer.synthesize(
                workflow_name=workflow_name,
                objective=workflow_description,
                steps=steps,
                ctx=ctx,
            )
            synthesized_markdown = synthesis.markdown
            synthesized_summary = synthesis.executive_summary
            if synthesis.conflicts:
                await _emit(
                    execution_id,
                    "synthesis_conflict_detected",
                    "system",
                    t("synthesis_conflicts", count=len(synthesis.conflicts)),
                    conflicts=synthesis.conflicts,
                    evidenceRanked=synthesis.evidence_ranked,
                )
            ctx.set_workflow_memory(
                "final_synthesis",
                {
                    "markdown": synthesized_markdown,
                    "executiveSummary": synthesized_summary,
                    "actionableAnswer": synthesis.actionable_answer,
                    "keyFindings": synthesis.key_findings,
                    "risks": synthesis.risks,
                    "recommendations": synthesis.recommendations,
                    "toolCount": synthesis.tool_count,
                    "retryCount": synthesis.retry_count,
                    "qualityScore": synthesis.quality_score,
                    "conflicts": synthesis.conflicts,
                    "provenance": synthesis.provenance,
                    "confidenceSummary": synthesis.confidence_summary,
                },
            )
            await ctx.persist()
            RuntimePerformanceTracker.end_stage(execution_id, "synthesis", "system")
            await _emit(
                execution_id,
                "synthesis_completed",
                "system",
                t("final_report_ready"),
                synthesizedOutput=synthesized_markdown,
                executiveSummary=synthesized_summary,
                actionableAnswer=synthesis.actionable_answer,
                keyFindings=synthesis.key_findings,
                risks=synthesis.risks,
                recommendations=synthesis.recommendations,
                qualityScore=synthesis.quality_score,
                provenance=synthesis.provenance,
                confidenceSummary=synthesis.confidence_summary,
            )
        except Exception as exc:
            logger.warning("Final synthesis failed for %s: %s", execution_id, exc)

        completion_log = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": completed_now,
            "level": "info",
            "agentName": "system",
            "content": t("workflow_completed", name=workflow_name),
        }
        logs.append(completion_log)

        await _emit(
            execution_id,
            "log",
            "system",
            t("workflow_completed", name=workflow_name),
            logId=completion_log["id"],
            level="info",
        )

        await _emit(
            execution_id,
            "workflow_completed",
            "system",
            t("workflow_completed", name=workflow_name),
            executionId=execution_id,
            totalAgents=agent_count,
            duration=(
                datetime.now(timezone.utc) - datetime.fromisoformat(now)
            ).total_seconds(),
            synthesizedOutput=synthesized_markdown or None,
        )

        all_outputs = ctx.get_all_outputs()
        agent_outputs = []
        for s in steps:
            if s.get("output"):
                agent_outputs.append(
                    f"{s['agentName']}: {s['output'][:200]}"
                )
        summary_text = synthesized_summary or "\n".join(agent_outputs[:10])

        output_payload = dict(all_outputs)
        if synthesized_markdown:
            output_payload["synthesized"] = synthesized_markdown
            output_payload["executive_summary"] = synthesized_summary

        await db.execute(
            """UPDATE executions
               SET status = 'completed', completed_at = ?,
                   output = ?, summary = ?
               WHERE id = ?""",
            (
                completed_now,
                json.dumps(output_payload),
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

    # Cognitive reflection + self-improvement (bounded, post-execution)
    runtime.lifecycle.set_phase(ExecutionPhase.POST_EXECUTION)
    if workflow_description.strip():
        try:
            from app.cognition.cognitive_orchestrator import CognitiveOrchestrator

            await CognitiveOrchestrator.run_post_execution(
                execution_id=execution_id,
                objective=workflow_description,
                status=final_status,
                ctx=ctx,
                emit_fn=_emit,
            )
        except Exception as exc:
            logger.warning("Cognitive post-execution failed for %s: %s", execution_id, exc)

    # Analytics + memory lifecycle + performance snapshot
    started_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if started_dt.tzinfo is None:
        started_dt = started_dt.replace(tzinfo=timezone.utc)
    duration = (datetime.now(timezone.utc) - started_dt).total_seconds()
    retry_count = int(ctx.get_workflow_memory("global_retry_count") or 0)
    tool_calls = ctx.get_workflow_memory("tool_calls") or []
    cache_hits = int(ctx.get_workflow_memory("tool_cache_hits") or 0)
    perf_snapshot: dict[str, Any] | None = None
    try:
        perf_snapshot = RuntimePerformanceTracker.finalize(execution_id)
        if perf_snapshot:
            await RuntimePerformanceTracker.persist_snapshot(execution_id, perf_snapshot)
            await _emit(
                execution_id,
                "performance_summary",
                "system",
                f"Runtime: {perf_snapshot.get('totalDurationSeconds', 0)}s — "
                f"{len(perf_snapshot.get('bottlenecks', []))} bottleneck(s)",
                **perf_snapshot,
            )
        await runtime.telemetry.emit_diagnostics(ctx, _emit, perf_snapshot)
        ctx.set_workflow_memory(
            "lifecycle_transitions",
            runtime.lifecycle.replay_transitions(),
        )
        await ExecutionAnalytics.record_execution_complete(
            execution_id=execution_id,
            status=final_status,
            duration_seconds=duration,
            retry_count=retry_count,
            tool_count=len(tool_calls) if isinstance(tool_calls, list) else 0,
            cache_hits=cache_hits,
            failure_reason=final_error,
        )
        shared = ctx.get_state()
        compact = await MemoryLifecycleManager.compact_execution_memory(
            execution_id, shared.get("workflow", shared)
        )
        await db.execute(
            "UPDATE executions SET shared_memory = ? WHERE id = ?",
            (json.dumps(compact), execution_id),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Post-execution analytics/memory failed: %s", exc)

    if workflow_description.strip():
        try:
            from app.self_improvement.self_improvement_orchestrator import (
                SelfImprovementOrchestrator,
            )

            if SelfImprovementOrchestrator.is_enabled():
                if perf_snapshot:
                    ctx.set_workflow_memory("performance_snapshot", perf_snapshot)
                    await ctx.persist()
                await SelfImprovementOrchestrator.run_post_execution_cycle(
                    execution_id=execution_id,
                    objective=workflow_description,
                    status=final_status,
                    ctx=ctx,
                    emit_fn=_emit,
                )
        except Exception as exc:
            logger.warning("Self-improvement cycle failed for %s: %s", execution_id, exc)

    try:
        from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

        await InfrastructureCoordinator.get_instance().on_execution_complete(
            execution_id, status=final_status
        )
    except Exception as exc:
        logger.debug("Infrastructure complete hook skipped: %s", exc)

    ProductionSafetyGuard.clear_execution(execution_id)
    ExecutionStabilityMonitor.cleanup_execution(execution_id)
    ExecutionRuntime.cleanup(execution_id)
    ExecutionKernel.cleanup(execution_id)
    MemoryRuntime.cleanup(execution_id)

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

    # Recreate stream and consolidated runtime
    await event_manager.create_stream(execution_id)

    runtime = ExecutionRuntime(execution_id, ctx)
    kernel = ExecutionKernel.for_execution(
        execution_id, ctx, workflow_id=workflow_id
    )
    kernel.bind_runtime(runtime)
    await kernel.initialize(_emit)
    ctx.set_workflow_memory("node_count", agent_count)
    runtime.lifecycle.set_phase(ExecutionPhase.EXECUTING)

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

    # Continue with adaptive scheduler from checkpoint position
    final_status, final_error, sorted_nodes = await _execute_nodes_adaptive(
        execution_id=execution_id,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        workflow_description=workflow_description,
        sorted_nodes=sorted_nodes,
        edges=edges,
        ctx=ctx,
        logs=logs,
        steps=steps,
        agent_timeout=agent_timeout,
        start_after_idx=current_idx,
    )
    agent_count = len(sorted_nodes)

    if final_status == "waiting_approval":
        return {
            "executionId": execution_id,
            "status": "waiting_approval",
            "steps": steps,
            "logs": logs,
            "error": None,
        }

    # Finalise
    completed_now = datetime.now(timezone.utc).isoformat()

    if final_status == "completed":
        await _record_runtime_state(
            execution_id=execution_id,
            to_state=RuntimeState.COMPLETED,
            reason=t("workflow_completed_reason"),
            metadata={"workflowName": workflow_name},
        )

        synthesized_markdown = ""
        synthesized_summary = ""
        try:
            await _emit(
                execution_id,
                "synthesis_started",
                "system",
                t("synthesizing"),
            )
            RuntimePerformanceTracker.begin_stage(execution_id, "synthesis", "system")
            synthesis = FinalResponseSynthesizer.synthesize(
                workflow_name=workflow_name,
                objective=workflow_description,
                steps=steps,
                ctx=ctx,
            )
            synthesized_markdown = synthesis.markdown
            synthesized_summary = synthesis.executive_summary
            if synthesis.conflicts:
                await _emit(
                    execution_id,
                    "synthesis_conflict_detected",
                    "system",
                    t("synthesis_conflicts", count=len(synthesis.conflicts)),
                    conflicts=synthesis.conflicts,
                    evidenceRanked=synthesis.evidence_ranked,
                )
            ctx.set_workflow_memory(
                "final_synthesis",
                {
                    "markdown": synthesized_markdown,
                    "executiveSummary": synthesized_summary,
                    "actionableAnswer": synthesis.actionable_answer,
                    "keyFindings": synthesis.key_findings,
                    "risks": synthesis.risks,
                    "recommendations": synthesis.recommendations,
                    "toolCount": synthesis.tool_count,
                    "retryCount": synthesis.retry_count,
                    "qualityScore": synthesis.quality_score,
                    "conflicts": synthesis.conflicts,
                    "provenance": synthesis.provenance,
                    "confidenceSummary": synthesis.confidence_summary,
                },
            )
            await ctx.persist()
            RuntimePerformanceTracker.end_stage(execution_id, "synthesis", "system")
            await _emit(
                execution_id,
                "synthesis_completed",
                "system",
                t("final_report_ready"),
                synthesizedOutput=synthesized_markdown,
                executiveSummary=synthesized_summary,
                actionableAnswer=synthesis.actionable_answer,
                keyFindings=synthesis.key_findings,
                risks=synthesis.risks,
                recommendations=synthesis.recommendations,
                qualityScore=synthesis.quality_score,
                provenance=synthesis.provenance,
                confidenceSummary=synthesis.confidence_summary,
            )
        except Exception as exc:
            logger.warning("Final synthesis failed for %s: %s", execution_id, exc)

        await _emit(
            execution_id,
            "workflow_completed",
            "system",
            t("workflow_completed", name=workflow_name),
            executionId=execution_id,
            totalAgents=agent_count,
            duration=0,
            synthesizedOutput=synthesized_markdown or None,
        )

        all_outputs = ctx.get_all_outputs()
        agent_outputs = []
        for s in steps:
            if s.get("output"):
                agent_outputs.append(
                    f"{s['agentName']}: {s['output'][:200]}"
                )
        summary_text = synthesized_summary or "\n".join(agent_outputs[:10])

        output_payload = dict(all_outputs)
        if synthesized_markdown:
            output_payload["synthesized"] = synthesized_markdown
            output_payload["executive_summary"] = synthesized_summary

        await db.execute(
            """UPDATE executions
               SET status = 'completed', completed_at = ?,
                   output = ?, summary = ?
               WHERE id = ?""",
            (
                completed_now,
                json.dumps(output_payload),
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

    try:
        perf_snapshot = RuntimePerformanceTracker.finalize(execution_id)
        await runtime.telemetry.emit_diagnostics(ctx, _emit, perf_snapshot)
    except Exception as exc:
        logger.warning("Resume telemetry failed: %s", exc)

    ExecutionStabilityMonitor.cleanup_execution(execution_id)
    ExecutionRuntime.cleanup(execution_id)
    MemoryRuntime.cleanup(execution_id)

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
