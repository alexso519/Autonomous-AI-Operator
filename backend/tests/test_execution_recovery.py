"""
Tests for execution persistence and recovery.
Run with: python -m pytest backend/tests/test_execution_recovery.py -v
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


async def _setup_db():
    from app.database.database import get_db

    await get_db()


async def _teardown_db():
    from app.database.database import close_db

    await close_db()


def test_persistence_create_and_get():
    async def run():
        await _setup_db()
        from app.infrastructure.execution_persistence import ExecutionPersistence

        record = await ExecutionPersistence.create("exec_p1", "wf_p1")
        assert record.execution_id == "exec_p1"
        loaded = await ExecutionPersistence.get("exec_p1")
        assert loaded is not None
        await _teardown_db()

    asyncio.run(run())


def test_checkpoint_save_and_restore():
    async def run():
        await _setup_db()
        from app.infrastructure.checkpoint_store import CheckpointStore

        await CheckpointStore.save("exec_cp1", {"nodeIndex": 2}, label="mid-run", action_count=5)
        latest = await CheckpointStore.get_latest("exec_cp1")
        assert latest is not None
        restored = await CheckpointStore.restore("exec_cp1", latest.checkpoint_id)
        assert restored["nodeIndex"] == 2
        await _teardown_db()

    asyncio.run(run())


def test_recovery_restore():
    async def run():
        await _setup_db()
        from app.infrastructure.execution_persistence import ExecutionPersistence
        from app.infrastructure.execution_recovery import ExecutionRecovery

        await ExecutionPersistence.create("exec_r1", "wf_r1")
        result = await ExecutionRecovery.restore_execution("exec_r1")
        assert result is not None
        timeline = await ExecutionRecovery.get_recovery_timeline("exec_r1")
        assert len(timeline) >= 1
        await _teardown_db()

    asyncio.run(run())


def test_state_snapshot_capture():
    async def run():
        await _setup_db()
        from app.infrastructure.state_snapshot_manager import StateSnapshotManager

        snapshot = await StateSnapshotManager.capture(
            "exec_snap1", graph_state={"nodes": 3}
        )
        assert snapshot.snapshot_id
        lineage = await StateSnapshotManager.get_lineage("exec_snap1")
        assert len(lineage) == 1
        await _teardown_db()

    asyncio.run(run())
