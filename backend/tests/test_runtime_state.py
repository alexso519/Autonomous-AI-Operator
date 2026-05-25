"""
Tests for runtime state machine persistence.
Run with: python backend/tests/test_runtime_state.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
import importlib.util
from app.database import database as dbmod


def _load_runtime_state_module():
    module_path = (
        Path(__file__).parent.parent
        / "app"
        / "execution"
        / "runtime_state.py"
    )
    spec = importlib.util.spec_from_file_location(
        "runtime_state_module",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load runtime_state module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _setup_in_memory_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await dbmod._init_tables(conn)
    dbmod._db = conn
    return conn


async def test_runtime_state_transitions() -> None:
    conn = await _setup_in_memory_db()
    execution_id = "exec-test-001"
    start_time = "2025-01-01T00:00:00Z"

    await conn.execute(
        """INSERT INTO executions
           (id, workflow_id, workflow_name, status, started_at, output)
           VALUES (?, ?, ?, 'running', ?, '{}')""",
        (execution_id, "wf-1", "Test Workflow", start_time),
    )
    await conn.commit()

    runtime_state = _load_runtime_state_module()
    RuntimeState = runtime_state.RuntimeState
    get_latest_state = runtime_state.get_latest_state
    transition_execution_state = runtime_state.transition_execution_state
    get_state_history = runtime_state.get_state_history

    current_state = await get_latest_state(execution_id)
    assert current_state == RuntimeState.RUNNING

    await transition_execution_state(
        execution_id=execution_id,
        to_state=RuntimeState.WAITING_APPROVAL,
        reason="Paused for human review",
        metadata={"reason": "Unit test approval"},
    )

    history = await get_state_history(execution_id)
    assert len(history) >= 1
    assert history[-1]["toState"] == RuntimeState.WAITING_APPROVAL.value
    assert history[-1]["fromState"] in {
        RuntimeState.CREATED.value,
        RuntimeState.RUNNING.value,
    }
    assert history[-1]["reason"] == "Paused for human review"

    latest_state = await get_latest_state(execution_id)
    assert latest_state == RuntimeState.WAITING_APPROVAL

    await conn.close()


if __name__ == "__main__":
    asyncio.run(test_runtime_state_transitions())
