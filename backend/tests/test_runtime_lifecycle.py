"""Tests for runtime lifecycle manager."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
from app.database import database as dbmod

_lifecycle_path = Path(__file__).parent.parent / "app" / "execution" / "runtime_lifecycle.py"
_spec = importlib.util.spec_from_file_location("runtime_lifecycle_test", _lifecycle_path)
_lifecycle_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _lifecycle_mod
assert _spec.loader is not None
_spec.loader.exec_module(_lifecycle_mod)

ExecutionPhase = _lifecycle_mod.ExecutionPhase
LifecycleManager = _lifecycle_mod.LifecycleManager

_state_path = Path(__file__).parent.parent / "app" / "execution" / "runtime_state.py"
_state_spec = importlib.util.spec_from_file_location("runtime_state_test", _state_path)
_state_mod = importlib.util.module_from_spec(_state_spec)
sys.modules[_state_spec.name] = _state_mod
assert _state_spec.loader is not None
_state_spec.loader.exec_module(_state_mod)
RuntimeState = _state_mod.RuntimeState


async def _setup_in_memory_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await dbmod._init_tables(conn)
    dbmod._db = conn
    return conn


async def test_lifecycle_deterministic_transitions() -> None:
    conn = await _setup_in_memory_db()
    execution_id = "exec-lifecycle-001"
    await conn.execute(
        """INSERT INTO executions
           (id, workflow_id, workflow_name, status, started_at, output)
           VALUES (?, ?, ?, 'running', '2025-01-01T00:00:00Z', '{}')""",
        (execution_id, "wf-1", "Test Workflow"),
    )
    await conn.commit()

    lifecycle = LifecycleManager(execution_id)
    await lifecycle.initialize(
        workflow_id="wf-1",
        initial_state=RuntimeState.RUNNING,
        reason="Test init",
    )

    state = await lifecycle.transition(
        RuntimeState.WAITING_APPROVAL,
        reason="Paused for approval",
        phase=ExecutionPhase.WAITING_APPROVAL,
    )
    assert state == RuntimeState.WAITING_APPROVAL
    assert lifecycle.current_phase == ExecutionPhase.WAITING_APPROVAL

    history = lifecycle.get_transition_history()
    assert len(history) >= 1
    assert history[-1].to_state == RuntimeState.WAITING_APPROVAL

    await conn.close()
    LifecycleManager.cleanup(execution_id)


async def test_lifecycle_blocks_recursive_transitions() -> None:
    execution_id = "exec-lifecycle-recursive"
    lifecycle = LifecycleManager(execution_id)
    lifecycle._ctx._transition_lock = True

    state = await lifecycle.transition(
        RuntimeState.FAILED,
        reason="Should be blocked",
    )
    assert len(lifecycle.get_transition_history()) == 0

    LifecycleManager.cleanup(execution_id)


async def test_lifecycle_replay_support() -> None:
    execution_id = "exec-lifecycle-replay"
    lifecycle = LifecycleManager(execution_id)
    lifecycle._ctx.current_state = RuntimeState.RUNNING

    await lifecycle.transition(RuntimeState.COMPLETED, reason="Done", phase=ExecutionPhase.COMPLETED)

    replay = lifecycle.replay_transitions()
    assert len(replay) == 1
    assert replay[0]["toState"] == "completed"
    assert replay[0]["phase"] == "completed"

    LifecycleManager.cleanup(execution_id)


async def test_lifecycle_validates_transitions() -> None:
    execution_id = "exec-lifecycle-validate"
    lifecycle = LifecycleManager(execution_id)
    lifecycle._ctx.current_state = RuntimeState.COMPLETED

    assert lifecycle.is_valid_transition(RuntimeState.COMPLETED, RuntimeState.RUNNING) is False
    assert lifecycle.is_valid_transition(RuntimeState.RUNNING, RuntimeState.COMPLETED) is True

    LifecycleManager.cleanup(execution_id)


if __name__ == "__main__":
    asyncio.run(test_lifecycle_deterministic_transitions())
    asyncio.run(test_lifecycle_blocks_recursive_transitions())
    asyncio.run(test_lifecycle_replay_support())
    asyncio.run(test_lifecycle_validates_transitions())
    print("All runtime lifecycle tests passed.")
