"""
Orchestrator adapters — wrap existing orchestrators behind the unified kernel.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from app.execution.context_manager import ContextManager
from app.runtime.action_system import ActionContext, ActionResult, ActionType, RuntimeAction

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class ResearchOrchestratorAdapter:
    """Adapter for ToolOrchestrator research pipeline."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> ActionResult:
        from app.tools.tool_orchestrator import ToolOrchestrator

        objective = action.payload.get("objective", "")
        execution_id = action.context.execution_id
        try:
            await ToolOrchestrator.execute_research_pipeline(
                execution_id=execution_id,
                query=objective,
                ctx=ctx,
                emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("research_pipeline_completed", True)
            await ctx.persist()
            return ActionResult(action_id=action.id, success=True, output="research_complete")
        except Exception as exc:
            logger.warning("Research adapter failed: %s", exc)
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class CodingOrchestratorAdapter:
    """Adapter for CodingOrchestrator pipeline."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> ActionResult:
        from app.coding.coding_orchestrator import CodingOrchestrator

        objective = action.payload.get("objective", "")
        execution_id = action.context.execution_id
        try:
            await CodingOrchestrator.execute_coding_pipeline(
                execution_id=execution_id,
                objective=objective,
                ctx=ctx,
                emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("coding_pipeline_completed", True)
            await ctx.persist()
            return ActionResult(action_id=action.id, success=True, output="coding_complete")
        except Exception as exc:
            logger.warning("Coding adapter failed: %s", exc)
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class BrowserOrchestratorAdapter:
    """Adapter for BrowserOrchestrator pipeline."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> ActionResult:
        from app.browser.browser_orchestrator import BrowserOrchestrator
        from app.computer_use.computer_use_orchestrator import ComputerUseOrchestrator

        objective = action.payload.get("objective", "")
        execution_id = action.context.execution_id
        try:
            if ComputerUseOrchestrator.is_computer_use_objective(objective):
                await ComputerUseOrchestrator.execute_computer_use_pipeline(
                    execution_id=execution_id,
                    objective=objective,
                    ctx=ctx,
                    emit_fn=emit_fn,
                )
                ctx.set_workflow_memory("computer_use_pipeline_completed", True)
            else:
                await BrowserOrchestrator.execute_browser_pipeline(
                    execution_id=execution_id,
                    objective=objective,
                    ctx=ctx,
                    emit_fn=emit_fn,
                )
            ctx.set_workflow_memory("browser_pipeline_completed", True)
            await ctx.persist()
            return ActionResult(action_id=action.id, success=True, output="browser_complete")
        except Exception as exc:
            logger.warning("Browser adapter failed: %s", exc)
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class ComputerUseOrchestratorAdapter:
    """Adapter for ComputerUseOrchestrator pipeline."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> ActionResult:
        from app.computer_use.computer_use_orchestrator import ComputerUseOrchestrator

        objective = action.payload.get("objective", "")
        execution_id = action.context.execution_id
        try:
            await ComputerUseOrchestrator.execute_computer_use_pipeline(
                execution_id=execution_id,
                objective=objective,
                ctx=ctx,
                emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("computer_use_pipeline_completed", True)
            await ctx.persist()
            return ActionResult(action_id=action.id, success=True, output="computer_use_complete")
        except Exception as exc:
            logger.warning("Computer use adapter failed: %s", exc)
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class ComputerWorkflowAdapter:
    """Adapter for long-running desktop workflow execution."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> ActionResult:
        from app.computer_use.action_planner import ActionPlanner
        from app.computer_use.workflow_executor import WorkflowExecutor

        objective = action.payload.get("objective", "")
        execution_id = action.context.execution_id
        steps = action.payload.get("steps")
        if not steps:
            plan = ActionPlanner.plan(objective)
            steps = [s.to_dict() for s in plan.steps]

        try:
            executor = WorkflowExecutor(execution_id)
            result = await executor.execute_workflow(
                objective,
                steps,
                chain_id=action.payload.get("chainId"),
                emit_fn=emit_fn,
                ctx=ctx,
            )
            ctx.set_workflow_memory("computer_workflow_completed", True)
            ctx.set_workflow_memory("computer_workflow_result", result)
            await ctx.persist()
            return ActionResult(
                action_id=action.id,
                success=result.get("success", False),
                output=result,
            )
        except Exception as exc:
            logger.warning("Computer workflow adapter failed: %s", exc)
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class ReflectionAdapter:
    """Adapter for ReflectionCoordinator."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        runtime: Any = None,
        graph: Any = None,
        sorted_nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> ActionResult:
        from app.execution.execution_runtime import ExecutionRuntime

        execution_id = action.context.execution_id
        rt = runtime or ExecutionRuntime.get(execution_id)
        if rt is None:
            rt = ExecutionRuntime(execution_id, ctx)
        else:
            rt.bind_context(ctx)

        node = action.payload.get("node", {})
        idx = action.payload.get("idx", 0)
        try:
            rt.telemetry.record_reflection()
            result = await rt.reflection.evaluate_and_emit(
                node=node,
                emit_fn=emit_fn,
                graph=graph,
                idx=idx,
                sorted_nodes=sorted_nodes or [],
                edges=edges or [],
                logs=logs or [],
            )
            return ActionResult(
                action_id=action.id,
                success=True,
                output=result,
                metadata={"replanned": result is not None},
            )
        except Exception as exc:
            return ActionResult(action_id=action.id, success=False, error=str(exc))


class RetryAdapter:
    """Adapter for RetryCoordinator."""

    @staticmethod
    async def execute(
        action: RuntimeAction,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        runtime: Any = None,
    ) -> ActionResult:
        from app.execution.contracts import RetryIntent, RetryRequest
        from app.execution.execution_runtime import ExecutionRuntime

        execution_id = action.context.execution_id
        rt = runtime or ExecutionRuntime.get(execution_id)
        if rt is None:
            return ActionResult(action_id=action.id, success=False, error="no_runtime")

        request = RetryRequest(
            node_id=action.context.node_id,
            intent=RetryIntent(action.payload.get("intent", "recovery")),
            reason=action.payload.get("reason", ""),
            quality_score=action.payload.get("quality_score"),
        )
        result = rt.retry.evaluate(request)
        if result.allowed:
            await emit_fn(
                execution_id,
                "retry_decision",
                action.context.agent_name,
                f"Retry allowed: {result.retry_type}",
                nodeId=action.context.node_id,
                allowed=True,
            )
        return ActionResult(
            action_id=action.id,
            success=result.allowed,
            output={
                "allowed": result.allowed,
                "blockReason": result.block_reason or "",
                "deduplicated": result.deduplicated,
            },
            metadata={"blockReason": result.block_reason or ""},
        )


def action_context_from_execution(
    execution_id: str,
    workflow_id: str = "",
    **kwargs: Any,
) -> ActionContext:
    return ActionContext(
        execution_id=execution_id,
        workflow_id=workflow_id,
        **kwargs,
    )


def emit_action_event(
    action: RuntimeAction,
    event_suffix: str,
) -> tuple[str, ActionType]:
    """Map action types to SSE event prefixes for telemetry."""
    mapping = {
        ActionType.WEB_RESEARCH: "research",
        ActionType.CODE_PATCH: "coding",
        ActionType.BROWSER_ACTION: "browser",
        ActionType.COMPUTER_USE_ACTION: "computer_use",
        ActionType.COMPUTER_WORKFLOW_ACTION: "computer_workflow",
        ActionType.REFLECTION: "reflection",
        ActionType.RETRY: "retry",
        ActionType.AGENT_EXECUTION: "agent",
    }
    prefix = mapping.get(action.action_type, "action")
    return f"{prefix}_{event_suffix}", action.action_type
