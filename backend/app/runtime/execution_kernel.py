"""
Unified execution kernel — single scheduler replacing fragmented orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from app.execution.context_manager import ContextManager
from app.execution.manager import execution_manager
from app.runtime.action_system import (
    ActionContext,
    ActionPriority,
    ActionResult,
    ActionStatus,
    ActionType,
    RuntimeAction,
)
from app.runtime.capability_registry import CapabilityRegistry
from app.runtime.kernel_adapters import (
    BrowserOrchestratorAdapter,
    CodingOrchestratorAdapter,
    ComputerUseOrchestratorAdapter,
    ComputerWorkflowAdapter,
    ReflectionAdapter,
    ResearchOrchestratorAdapter,
    RetryAdapter,
)
from app.runtime.runtime_bus import RuntimeBus, RuntimeChannel
from app.runtime.runtime_diagnostics import RuntimeDiagnostics
from app.runtime.runtime_state_store import RuntimeStateStore

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]
ActionHandler = Callable[[RuntimeAction], Awaitable[ActionResult]]


@dataclass
class KernelQueue:
    """Priority queue for pending actions."""

    _queues: dict[ActionPriority, deque[RuntimeAction]] = field(
        default_factory=lambda: {
            ActionPriority.CRITICAL: deque(),
            ActionPriority.HIGH: deque(),
            ActionPriority.NORMAL: deque(),
            ActionPriority.LOW: deque(),
        }
    )
    _running: set[str] = field(default_factory=set)
    _completed: set[str] = field(default_factory=set)
    _failed: set[str] = field(default_factory=set)

    def enqueue(self, action: RuntimeAction) -> None:
        self._queues[action.priority].append(action)

    def dequeue_ready(self, completed_deps: set[str]) -> RuntimeAction | None:
        for priority in (
            ActionPriority.CRITICAL,
            ActionPriority.HIGH,
            ActionPriority.NORMAL,
            ActionPriority.LOW,
        ):
            queue = self._queues[priority]
            for _ in range(len(queue)):
                action = queue.popleft()
                if action.is_cancelled:
                    continue
                deps_met = all(d in completed_deps for d in action.dependencies)
                if deps_met:
                    return action
                queue.append(action)
        return None

    @property
    def pending_count(self) -> int:
        return sum(len(q) for q in self._queues.values())

    @property
    def running_count(self) -> int:
        return len(self._running)


class DependencyResolver:
    """Resolves action dependency graph for DAG scheduling."""

    def __init__(self) -> None:
        self._graph: dict[str, list[str]] = defaultdict(list)
        self._reverse: dict[str, list[str]] = defaultdict(list)

    def add(self, action: RuntimeAction) -> None:
        self._graph[action.id] = list(action.dependencies)
        for dep in action.dependencies:
            self._reverse[dep].append(action.id)

    def get_ready_batch(
        self,
        actions: list[RuntimeAction],
        completed: set[str],
    ) -> list[RuntimeAction]:
        ready = []
        for action in actions:
            if action.id in completed:
                continue
            if all(d in completed for d in action.dependencies):
                ready.append(action)
        return ready

    def parallel_groups(
        self,
        actions: list[RuntimeAction],
    ) -> list[list[RuntimeAction]]:
        """Partition actions into parallelizable layers."""
        if not actions:
            return []
        action_map = {a.id: a for a in actions}
        completed: set[str] = set()
        layers: list[list[RuntimeAction]] = []

        while len(completed) < len(actions):
            layer = self.get_ready_batch(actions, completed)
            if not layer:
                remaining = [a for a in actions if a.id not in completed]
                if remaining:
                    layers.append(remaining)
                break
            layers.append(layer)
            for a in layer:
                completed.add(a.id)
        return layers


class PriorityScheduler:
    """Priority-aware scheduler with starvation prevention."""

    def __init__(self, max_concurrent: int = 4) -> None:
        self.max_concurrent = max_concurrent
        self._starvation_counter: dict[str, int] = defaultdict(int)

    def escalate_if_starved(self, action: RuntimeAction, wait_cycles: int) -> None:
        if wait_cycles > 3 and action.priority.value < ActionPriority.HIGH.value:
            order = [
                ActionPriority.LOW,
                ActionPriority.NORMAL,
                ActionPriority.HIGH,
                ActionPriority.CRITICAL,
            ]
            idx = order.index(action.priority)
            action.priority = order[min(idx + 1, len(order) - 1)]


class RuntimeDispatcher:
    """Routes actions to capability-specific handlers."""

    _ADAPTER_MAP: dict[ActionType, str] = {
        ActionType.WEB_RESEARCH: "research",
        ActionType.CODE_PATCH: "coding",
        ActionType.BROWSER_ACTION: "browser",
        ActionType.COMPUTER_USE_ACTION: "computer_use",
        ActionType.COMPUTER_WORKFLOW_ACTION: "computer_workflow",
        ActionType.REFLECTION: "reflection",
        ActionType.RETRY: "retry",
    }

    def __init__(
        self,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        runtime: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.ctx = ctx
        self.emit_fn = emit_fn
        self.runtime = runtime
        self.extra = extra or {}

    async def dispatch(self, action: RuntimeAction) -> ActionResult:
        if action.action_type == ActionType.WEB_RESEARCH:
            return await ResearchOrchestratorAdapter.execute(action, self.ctx, self.emit_fn)
        if action.action_type == ActionType.CODE_PATCH:
            return await CodingOrchestratorAdapter.execute(action, self.ctx, self.emit_fn)
        if action.action_type == ActionType.BROWSER_ACTION:
            return await BrowserOrchestratorAdapter.execute(action, self.ctx, self.emit_fn)
        if action.action_type == ActionType.COMPUTER_USE_ACTION:
            return await ComputerUseOrchestratorAdapter.execute(action, self.ctx, self.emit_fn)
        if action.action_type == ActionType.COMPUTER_WORKFLOW_ACTION:
            return await ComputerWorkflowAdapter.execute(action, self.ctx, self.emit_fn)
        if action.action_type == ActionType.REFLECTION:
            return await ReflectionAdapter.execute(
                action,
                self.ctx,
                self.emit_fn,
                runtime=self.runtime,
                graph=self.extra.get("graph"),
                sorted_nodes=self.extra.get("sorted_nodes"),
                edges=self.extra.get("edges"),
                logs=self.extra.get("logs"),
            )
        if action.action_type == ActionType.RETRY:
            return await RetryAdapter.execute(
                action, self.ctx, self.emit_fn, runtime=self.runtime
            )
        if action.action_type == ActionType.AGENT_EXECUTION:
            handler = action.payload.get("handler")
            if callable(handler):
                try:
                    output = await handler(action)
                    return ActionResult(action_id=action.id, success=True, output=output)
                except Exception as exc:
                    return ActionResult(action_id=action.id, success=False, error=str(exc))
        return ActionResult(
            action_id=action.id,
            success=False,
            error=f"no_handler_for_{action.action_type.value}",
        )


class ExecutionKernel:
    """
    Single runtime scheduler coordinating all orchestration subsystems.

    Wraps (does not replace):
      adaptive_graph, parallel_runtime, retry_coordinator,
      reflection_coordinator, tool/coding/browser orchestrators, memory_runtime
    """

    _instances: dict[str, ExecutionKernel] = {}

    def __init__(
        self,
        execution_id: str,
        ctx: ContextManager,
        *,
        workflow_id: str = "",
        max_concurrent: int = 4,
    ) -> None:
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.ctx = ctx
        self.max_concurrent = max_concurrent

        self.bus = RuntimeBus.for_execution(execution_id)
        self.registry = CapabilityRegistry.for_execution(execution_id)
        self.state_store = RuntimeStateStore.for_execution(execution_id)
        self.diagnostics = RuntimeDiagnostics.for_execution(execution_id)

        self.queue = KernelQueue()
        self.dependency_resolver = DependencyResolver()
        self.scheduler = PriorityScheduler(max_concurrent=max_concurrent)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._emit_fn: EmitFn | None = None
        self._dispatcher: RuntimeDispatcher | None = None
        self._completed_actions: set[str] = set()
        self._action_results: dict[str, ActionResult] = {}
        self._wait_cycles: dict[str, int] = defaultdict(int)
        self._initialized = False
        self._cancelled = False
        self._runtime: Any = None

    @classmethod
    def for_execution(
        cls,
        execution_id: str,
        ctx: ContextManager,
        **kwargs: Any,
    ) -> ExecutionKernel:
        if execution_id not in cls._instances:
            cls._instances[execution_id] = cls(execution_id, ctx, **kwargs)
        else:
            kernel = cls._instances[execution_id]
            kernel.ctx = ctx
        return cls._instances[execution_id]

    @classmethod
    def get(cls, execution_id: str) -> ExecutionKernel | None:
        return cls._instances.get(execution_id)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)
        RuntimeBus.cleanup(execution_id)
        CapabilityRegistry.cleanup(execution_id)
        RuntimeStateStore.cleanup(execution_id)
        RuntimeDiagnostics.cleanup(execution_id)

    def bind_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    async def initialize(self, emit_fn: EmitFn) -> None:
        if self._initialized:
            return
        self._emit_fn = emit_fn
        self.registry.set_emit_fn(emit_fn)
        self._dispatcher = RuntimeDispatcher(
            self.ctx, emit_fn, runtime=self._runtime
        )

        from app.runtime.compat.legacy_event_adapter import LegacyEventAdapter

        adapter = LegacyEventAdapter(self.bus, emit_fn, self.execution_id)
        adapter.install()

        self._initialized = True

        try:
            objective = str(self.ctx.get_workflow_memory("objective") or "")
            if objective.strip():
                from app.intelligence.knowledge_reasoning_coordinator import (
                    KnowledgeReasoningCoordinator,
                )

                kr = await KnowledgeReasoningCoordinator.run_pre_execution(
                    self.execution_id, objective, emit_fn=emit_fn,
                )
                self.ctx.set_workflow_memory("kernel_knowledge_reasoning", kr)
        except Exception as exc:
            logger.debug("Kernel knowledge reasoning hook skipped: %s", exc)

        await self.bus.publish(
            RuntimeChannel.TELEMETRY,
            "kernel_initialized",
            "Execution kernel initialized",
            capabilities=self.registry.stats(),
        )
        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "kernel_initialized",
                "system",
                "Unified execution kernel online",
                capabilities=self.registry.stats(),
            )

    def set_dispatcher_extra(self, **kwargs: Any) -> None:
        if self._dispatcher:
            self._dispatcher.extra.update(kwargs)

    async def schedule_action(self, action: RuntimeAction) -> str:
        action.status = ActionStatus.SCHEDULED
        self.dependency_resolver.add(action)
        self.queue.enqueue(action)
        self.diagnostics.record_action_scheduled()
        await self.state_store.record_action(action)

        await self.bus.publish(
            RuntimeChannel.EXECUTION,
            "action_scheduled",
            f"Scheduled {action.action_type.value}",
            actionId=action.id,
            actionType=action.action_type.value,
            nodeId=action.context.node_id,
            priority=action.priority.value,
        )
        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "action_scheduled",
                action.context.agent_name,
                f"Action scheduled: {action.action_type.value}",
                actionId=action.id,
                actionType=action.action_type.value,
            )
        return action.id

    async def dispatch_action(self, action: RuntimeAction) -> ActionResult:
        """Execute a single action through the kernel dispatcher."""
        if not self._initialized:
            raise RuntimeError("Kernel not initialized — call initialize() first")
        if execution_manager.is_cancellation_requested(self.execution_id):
            action.status = ActionStatus.CANCELLED
            self.diagnostics.record_action_cancelled()
            return ActionResult(action_id=action.id, success=False, error="cancelled")

        action.status = ActionStatus.RUNNING
        action.started_at = datetime.now(timezone.utc).isoformat()
        self.queue._running.add(action.id)
        self.diagnostics.record_action_started()
        await self.state_store.update_action_status(action.id, ActionStatus.RUNNING)

        start = time.monotonic()
        result: ActionResult

        async with self._semaphore:
            try:
                assert self._dispatcher is not None
                cap = self._ADAPTER_CAPABILITY.get(action.action_type)
                if cap:
                    await self.registry.select(cap)
                    self.diagnostics.capability_metrics.selections += 1

                result = await self._dispatcher.dispatch(action)

                if result.success:
                    action.status = ActionStatus.COMPLETED
                    self._completed_actions.add(action.id)
                    self.registry.record_success(cap or "")
                else:
                    if action.retry_count < action.max_retries:
                        action.retry_count += 1
                        action.status = ActionStatus.RETRYING
                        action.provenance.append(action.id)
                        self.diagnostics.record_retry_burst(action.retry_count)
                        result = await self.dispatch_action(action)
                    else:
                        action.status = ActionStatus.FAILED
                        self.queue._failed.add(action.id)
                        if cap:
                            self.registry.record_failure(cap)
                            self.diagnostics.record_capability_failure()
                            await self.bus.publish(
                                RuntimeChannel.TELEMETRY,
                                "capability_failure",
                                f"Capability failed: {cap}",
                                capabilityId=cap,
                                actionId=action.id,
                            )
                            if self._emit_fn:
                                await self._emit_fn(
                                    self.execution_id,
                                    "capability_failure",
                                    "system",
                                    f"Capability failure: {cap}",
                                    capabilityId=cap,
                                )
            except Exception as exc:
                logger.exception("Kernel dispatch failed for %s", action.id)
                action.status = ActionStatus.FAILED
                result = ActionResult(action_id=action.id, success=False, error=str(exc))

        elapsed_ms = (time.monotonic() - start) * 1000
        result.duration_ms = elapsed_ms
        action.completed_at = datetime.now(timezone.utc).isoformat()
        self.queue._running.discard(action.id)
        self._action_results[action.id] = result

        if result.success:
            self.diagnostics.record_action_completed(elapsed_ms)
        else:
            self.diagnostics.record_action_failed()

        await self.state_store.update_action_status(
            action.id,
            action.status,
            retry_lineage=action.provenance,
        )

        await self.bus.publish(
            RuntimeChannel.EXECUTION,
            "action_completed",
            f"Action {action.action_type.value} {'ok' if result.success else 'failed'}",
            actionId=action.id,
            success=result.success,
            durationMs=elapsed_ms,
        )
        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "action_completed",
                action.context.agent_name,
                f"Action completed: {action.action_type.value}",
                actionId=action.id,
                success=result.success,
                durationMs=elapsed_ms,
            )

        await self._maybe_emit_scheduler_pressure()
        return result

    _ADAPTER_CAPABILITY: dict[ActionType, str] = {
        ActionType.WEB_RESEARCH: "research",
        ActionType.CODE_PATCH: "coding",
        ActionType.BROWSER_ACTION: "browser",
        ActionType.COMPUTER_USE_ACTION: "computer_use",
        ActionType.COMPUTER_WORKFLOW_ACTION: "computer_workflow",
        ActionType.REFLECTION: "reflection",
        ActionType.RETRY: "retry",
    }

    async def schedule_pipeline_actions(
        self,
        workflow_description: str,
        *,
        include_research: bool = False,
        include_coding: bool = False,
        include_browser: bool = False,
        include_computer_use: bool = False,
        include_computer_workflow: bool = False,
    ) -> list[str]:
        """Schedule pre-execution pipeline actions based on objective type."""
        action_ids: list[str] = []
        base_ctx = ActionContext(
            execution_id=self.execution_id,
            workflow_id=self.workflow_id,
        )

        if include_coding and not self.ctx.get_workflow_memory("coding_pipeline_completed"):
            action = RuntimeAction(
                action_type=ActionType.CODE_PATCH,
                context=base_ctx,
                payload={"objective": workflow_description},
                priority=ActionPriority.HIGH,
            )
            action_ids.append(await self.schedule_action(action))

        if include_computer_workflow and not self.ctx.get_workflow_memory("computer_workflow_completed"):
            action = RuntimeAction(
                action_type=ActionType.COMPUTER_WORKFLOW_ACTION,
                context=base_ctx,
                payload={"objective": workflow_description},
                priority=ActionPriority.HIGH,
            )
            action_ids.append(await self.schedule_action(action))
        elif include_computer_use and not self.ctx.get_workflow_memory("computer_use_pipeline_completed"):
            action = RuntimeAction(
                action_type=ActionType.COMPUTER_USE_ACTION,
                context=base_ctx,
                payload={"objective": workflow_description},
                priority=ActionPriority.HIGH,
            )
            action_ids.append(await self.schedule_action(action))
        elif include_browser and not self.ctx.get_workflow_memory("browser_pipeline_completed"):
            action = RuntimeAction(
                action_type=ActionType.BROWSER_ACTION,
                context=base_ctx,
                payload={"objective": workflow_description},
                priority=ActionPriority.HIGH,
            )
            action_ids.append(await self.schedule_action(action))

        if include_research and not self.ctx.get_workflow_memory("research_pipeline_completed"):
            action = RuntimeAction(
                action_type=ActionType.WEB_RESEARCH,
                context=base_ctx,
                payload={"objective": workflow_description},
                priority=ActionPriority.HIGH,
            )
            action_ids.append(await self.schedule_action(action))

        return action_ids

    async def run_scheduled_pipelines(self) -> list[ActionResult]:
        """Execute all pending pipeline actions in dependency order."""
        results: list[ActionResult] = []
        pending = self._collect_pending()
        layers = self.dependency_resolver.parallel_groups(pending)

        for layer in layers:
            if len(layer) == 1:
                results.append(await self.dispatch_action(layer[0]))
            else:
                batch = await asyncio.gather(
                    *[self.dispatch_action(a) for a in layer],
                    return_exceptions=True,
                )
                for item in batch:
                    if isinstance(item, ActionResult):
                        results.append(item)
                    elif isinstance(item, Exception):
                        logger.error("Parallel dispatch error: %s", item)

        return results

    def _collect_pending(self) -> list[RuntimeAction]:
        pending: list[RuntimeAction] = []
        for q in self.queue._queues.values():
            pending.extend(list(q))
        return [a for a in pending if a.id not in self._completed_actions]

    async def schedule_agent_batch(
        self,
        actions: list[RuntimeAction],
    ) -> list[ActionResult]:
        """Schedule and execute a batch of agent actions with parallel groups."""
        for action in actions:
            await self.schedule_action(action)
        layers = self.dependency_resolver.parallel_groups(actions)
        results: list[ActionResult] = []

        for layer in layers:
            if len(layer) <= 1:
                for a in layer:
                    results.append(await self.dispatch_action(a))
            else:
                await self.bus.publish(
                    RuntimeChannel.EXECUTION,
                    "parallel_group_started",
                    f"Parallel batch: {len(layer)} actions",
                    agents=[a.context.agent_name for a in layer],
                )
                batch = await asyncio.gather(
                    *[self.dispatch_action(a) for a in layer],
                    return_exceptions=True,
                )
                for item in batch:
                    if isinstance(item, ActionResult):
                        results.append(item)

        return results

    async def checkpoint(
        self,
        status: str,
        *,
        graph_state: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        await self.state_store.save_checkpoint(
            self.workflow_id,
            status,
            graph_state=graph_state,
            memory_state=self.ctx.get_state(),
            reason=reason,
        )

    async def recover(self) -> bool:
        """Attempt recovery from latest checkpoint."""
        checkpoint = self.state_store.get_latest_checkpoint()
        if not checkpoint:
            return False

        await self.bus.publish(
            RuntimeChannel.TELEMETRY,
            "runtime_recovered",
            f"Recovered from checkpoint {checkpoint.checkpoint_id}",
            checkpointId=checkpoint.checkpoint_id,
        )
        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "runtime_recovered",
                "system",
                "Runtime recovered from checkpoint",
                checkpointId=checkpoint.checkpoint_id,
            )
        self.diagnostics.record_degraded_mode()
        return True

    async def _maybe_emit_scheduler_pressure(self) -> None:
        snap = self.diagnostics.snapshot(bus=self.bus, registry=self.registry)
        pressure = snap["queue"]["pressureRatio"]
        if pressure > 0.7:
            await self.bus.publish(
                RuntimeChannel.TELEMETRY,
                "scheduler_pressure",
                f"Scheduler pressure at {pressure:.0%}",
                pressureRatio=pressure,
                pending=snap["queue"]["pending"],
            )
            if self._emit_fn:
                await self._emit_fn(
                    self.execution_id,
                    "scheduler_pressure",
                    "system",
                    f"Scheduler pressure: {pressure:.0%}",
                    pressureRatio=pressure,
                )

    def get_diagnostics_snapshot(self) -> dict[str, Any]:
        return self.diagnostics.snapshot(bus=self.bus, registry=self.registry)

    def replay_actions(self) -> list[dict[str, Any]]:
        return self.state_store.replay_actions()
