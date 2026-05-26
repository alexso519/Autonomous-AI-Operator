"""Tests for unified execution kernel."""

import asyncio
from types import ModuleType
from unittest.mock import AsyncMock, patch

# Stub crewai before imports that may pull engine
if "crewai" not in __import__("sys").modules:
    crewai = ModuleType("crewai")
    crewai.Agent = type("Agent", (), {})
    crewai.Task = type("Task", (), {})
    __import__("sys").modules["crewai"] = crewai

from app.execution.context_manager import ContextManager
from app.runtime.action_system import (
    ActionContext,
    ActionPriority,
    ActionResult,
    ActionType,
    RuntimeAction,
)
from app.runtime.execution_kernel import (
    DependencyResolver,
    ExecutionKernel,
    KernelQueue,
    PriorityScheduler,
)


def test_kernel_initialize():
    async def _run():
        ctx = ContextManager(execution_id="kernel-test-1")
        kernel = ExecutionKernel.for_execution("kernel-test-1", ctx, workflow_id="wf-1")
        try:
            emit = AsyncMock()
            await kernel.initialize(emit)
            assert kernel._initialized is True
            emit.assert_called()
            assert emit.call_args_list[-1][0][1] == "kernel_initialized"
        finally:
            ExecutionKernel.cleanup("kernel-test-1")

    asyncio.run(_run())


def test_schedule_and_dispatch_research():
    async def _run():
        ctx = ContextManager(execution_id="kernel-test-2")
        kernel = ExecutionKernel.for_execution("kernel-test-2", ctx, workflow_id="wf-1")
        try:
            emit = AsyncMock()
            await kernel.initialize(emit)

            with patch(
                "app.runtime.kernel_adapters.ResearchOrchestratorAdapter.execute",
                new_callable=AsyncMock,
                return_value=ActionResult(action_id="x", success=True),
            ):
                action = RuntimeAction(
                    action_type=ActionType.WEB_RESEARCH,
                    context=ActionContext(execution_id="kernel-test-2"),
                    payload={"objective": "test query"},
                    priority=ActionPriority.HIGH,
                )
                await kernel.schedule_action(action)
                result = await kernel.dispatch_action(action)
                assert result.success is True
        finally:
            ExecutionKernel.cleanup("kernel-test-2")

    asyncio.run(_run())


def test_dependency_resolver_parallel_groups():
    resolver = DependencyResolver()
    a1 = RuntimeAction(
        action_type=ActionType.AGENT_EXECUTION,
        context=ActionContext(execution_id="e1"),
        id="a1",
    )
    a2 = RuntimeAction(
        action_type=ActionType.AGENT_EXECUTION,
        context=ActionContext(execution_id="e1"),
        id="a2",
        dependencies=["a1"],
    )
    layers = resolver.parallel_groups([a1, a2])
    assert len(layers) == 2
    assert layers[0][0].id == "a1"


def test_kernel_queue_priority():
    queue = KernelQueue()
    low = RuntimeAction(
        action_type=ActionType.TOOL_CALL,
        context=ActionContext(execution_id="e1"),
        priority=ActionPriority.LOW,
    )
    high = RuntimeAction(
        action_type=ActionType.TOOL_CALL,
        context=ActionContext(execution_id="e1"),
        priority=ActionPriority.HIGH,
    )
    queue.enqueue(low)
    queue.enqueue(high)
    ready = queue.dequeue_ready(set())
    assert ready is not None
    assert ready.priority == ActionPriority.HIGH


def test_priority_scheduler_escalation():
    sched = PriorityScheduler()
    action = RuntimeAction(
        action_type=ActionType.AGENT_EXECUTION,
        context=ActionContext(execution_id="e1"),
        priority=ActionPriority.LOW,
    )
    sched.escalate_if_starved(action, wait_cycles=5)
    assert action.priority.value >= ActionPriority.NORMAL.value


def test_recovery_after_checkpoint():
    async def _run():
        ctx = ContextManager(execution_id="kernel-test-3")
        kernel = ExecutionKernel.for_execution("kernel-test-3", ctx, workflow_id="wf-1")
        try:
            emit = AsyncMock()
            await kernel.initialize(emit)
            await kernel.checkpoint("running", graph_state={"step": 1})
            recovered = await kernel.recover()
            assert recovered is True
        finally:
            ExecutionKernel.cleanup("kernel-test-3")

    asyncio.run(_run())


def test_parallel_research_synthesis_workflow():
    async def _run():
        ctx = ContextManager(execution_id="kernel-test-4")
        kernel = ExecutionKernel.for_execution("kernel-test-4", ctx, workflow_id="wf-1")
        try:
            emit = AsyncMock()
            await kernel.initialize(emit)

            research = RuntimeAction(
                action_type=ActionType.WEB_RESEARCH,
                context=ActionContext(execution_id="kernel-test-4"),
                id="r1",
                payload={"objective": "topic"},
            )
            synthesis = RuntimeAction(
                action_type=ActionType.SYNTHESIS,
                context=ActionContext(execution_id="kernel-test-4"),
                id="s1",
                dependencies=["r1"],
                payload={"handler": AsyncMock(return_value="report")},
            )

            with patch.object(kernel, "dispatch_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.return_value = ActionResult(action_id="x", success=True)
                await kernel.schedule_agent_batch([research, synthesis])
                assert mock_dispatch.call_count >= 1
        finally:
            ExecutionKernel.cleanup("kernel-test-4")

    asyncio.run(_run())


def test_backpressure_under_many_actions():
    async def _run():
        ctx = ContextManager(execution_id="kernel-test-5")
        kernel = ExecutionKernel.for_execution("kernel-test-5", ctx, workflow_id="wf-1")
        try:
            emit = AsyncMock()
            await kernel.initialize(emit)
            kernel.bus.queue_size = 8

            from app.runtime.runtime_bus import RuntimeChannel

            for i in range(20):
                await kernel.bus.publish(
                    RuntimeChannel.TELEMETRY,
                    f"evt_{i}",
                    f"msg {i}",
                    skip_dedupe=True,
                )

            assert kernel.bus.stats["publishCount"] >= 8
        finally:
            ExecutionKernel.cleanup("kernel-test-5")

    asyncio.run(_run())
