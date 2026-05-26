"""Tests for runtime state store."""

import asyncio

from app.runtime.action_system import (
    ActionContext,
    ActionStatus,
    ActionType,
    RuntimeAction,
)
from app.runtime.runtime_state_store import RuntimeStateStore


def test_record_action():
    async def _run():
        store = RuntimeStateStore("state-test-1")
        action = RuntimeAction(
            action_type=ActionType.AGENT_EXECUTION,
            context=ActionContext(execution_id="state-test-1", node_id="n1"),
        )
        await store.record_action(action)
        history = store.get_action_history()
        assert len(history) == 1
        assert history[0].node_id == "n1"

    asyncio.run(_run())


def test_update_action_status():
    async def _run():
        store = RuntimeStateStore("state-test-2")
        action = RuntimeAction(
            action_type=ActionType.TOOL_CALL,
            context=ActionContext(execution_id="state-test-2"),
        )
        await store.record_action(action)
        await store.update_action_status(action.id, ActionStatus.COMPLETED)
        assert store._actions[action.id].status == "completed"

    asyncio.run(_run())


def test_checkpoint():
    async def _run():
        store = RuntimeStateStore("state-test-3")
        cp = await store.save_checkpoint(
            workflow_id="wf-1",
            status="running",
            graph_state={"nodes": 3},
            memory_state={"key": "val"},
            reason="test",
        )
        assert cp.checkpoint_id
        latest = store.get_latest_checkpoint()
        assert latest is not None
        assert latest.snapshot.workflow_id == "wf-1"

    asyncio.run(_run())


def test_replay_actions():
    async def _run():
        store = RuntimeStateStore("state-test-4")
        action = RuntimeAction(
            action_type=ActionType.SYNTHESIS,
            context=ActionContext(execution_id="state-test-4"),
        )
        await store.record_action(action)
        replay = store.replay_actions()
        assert len(replay) == 1
        assert replay[0]["actionType"] == "synthesis"

    asyncio.run(_run())


def test_recovery_lineage():
    async def _run():
        store = RuntimeStateStore("state-test-5")
        await store.record_recovery_lineage("cp-1", "act-1", "kernel_failure")
        assert store.stats()["lineageCount"] == 1

    asyncio.run(_run())
