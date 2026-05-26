"""
Tests for workflow executor.
Run with: python -m pytest backend/tests/test_workflow_executor.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.task_chain_manager import TaskChainManager
from app.computer_use.action_scheduler import ActionScheduler
from app.computer_use.recovery_executor import RecoveryExecutor


async def _run_task_chain():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    mgr = TaskChainManager("test-wf", "chain-test-1")
    steps = [{"action": "observe", "target": "screen"}]
    await mgr.start("Test objective", steps, emit_fn=emit_fn)
    await mgr.checkpoint(0, {"step": {"success": True}})
    branch = await mgr.create_branch(1, "test branch", emit_fn=emit_fn)
    assert branch
    assert "workflow_branch_created" in emitted


def test_task_chain_manager():
    asyncio.run(_run_task_chain())


def test_action_scheduler():
    async def _run():
        sched = ActionScheduler("test-sched", min_interval_ms=0)
        action = await sched.schedule("click", "OK", step_index=0)
        assert action.action_type == "click"
        assert sched.pending_count() >= 0
        ActionScheduler.cleanup("test-sched")

    asyncio.run(_run())


async def _run_recovery():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    recovery = RecoveryExecutor("test-recovery")
    with patch.object(recovery, "_execute_strategy", new_callable=AsyncMock, return_value=True):
        result = await recovery.attempt_recovery("click", "OK", "grounding failed", emit_fn=emit_fn)
    assert "ui_recovery_attempted" in emitted
    assert result["attempts"] == 1


def test_recovery_executor():
    asyncio.run(_run_recovery())


async def _run_workflow_executor():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.to_dict.return_value = {"success": True}
    mock_result.error = ""

    with patch(
        "app.computer_use.multimodal_loop.MultimodalLoop"
    ) as MockLoop:
        instance = MockLoop.return_value
        instance.cycle.execute_step = AsyncMock(return_value=mock_result)

        from app.computer_use.workflow_executor import WorkflowExecutor
        executor = WorkflowExecutor("test-wf-exec")
        result = await executor.execute_workflow(
            "Observe screen",
            [{"action": "observe", "target": "screen"}],
            emit_fn=emit_fn,
        )
        assert "chainId" in result
        assert "workflow_chain_started" in emitted


def test_workflow_executor():
    asyncio.run(_run_workflow_executor())
