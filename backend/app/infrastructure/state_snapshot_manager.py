"""
State snapshot manager — full runtime state capture for long-running workflows.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)


@dataclass
class RuntimeSnapshot:
    snapshot_id: str
    execution_id: str
    graph_state: dict[str, Any] = field(default_factory=dict)
    memory_state: dict[str, Any] = field(default_factory=dict)
    kernel_state: dict[str, Any] = field(default_factory=dict)
    desktop_state: dict[str, Any] = field(default_factory=dict)
    lineage: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "executionId": self.execution_id,
            "graphState": self.graph_state,
            "memoryState": self.memory_state,
            "kernelState": self.kernel_state,
            "desktopState": self.desktop_state,
            "lineageCount": len(self.lineage),
            "createdAt": self.created_at,
        }


class StateSnapshotManager:
    """Captures and replays full runtime state across subsystems."""

    @staticmethod
    async def capture(
        execution_id: str,
        *,
        graph_state: dict[str, Any] | None = None,
        memory_state: dict[str, Any] | None = None,
        kernel_state: dict[str, Any] | None = None,
        desktop_state: dict[str, Any] | None = None,
        lineage: list[dict[str, Any]] | None = None,
    ) -> RuntimeSnapshot:
        snapshot = RuntimeSnapshot(
            snapshot_id=uuid.uuid4().hex[:16],
            execution_id=execution_id,
            graph_state=graph_state or {},
            memory_state=memory_state or {},
            kernel_state=kernel_state or {},
            desktop_state=desktop_state or {},
            lineage=lineage or [],
        )

        db = await get_db()
        await db.execute(
            """INSERT INTO execution_lineage
               (id, execution_id, snapshot_data, lineage_data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                snapshot.snapshot_id,
                execution_id,
                json.dumps(snapshot.to_dict()),
                json.dumps(snapshot.lineage),
                snapshot.created_at,
            ),
        )
        await db.commit()

        await emit_infrastructure_event(
            execution_id,
            "runtime_snapshot_created",
            f"Runtime snapshot {snapshot.snapshot_id} captured",
            snapshotId=snapshot.snapshot_id,
        )
        return snapshot

    @staticmethod
    async def capture_from_kernel(execution_id: str) -> RuntimeSnapshot:
        """Integrate with RuntimeStateStore and MemoryRuntime."""
        graph_state: dict[str, Any] = {}
        kernel_state: dict[str, Any] = {}
        memory_state: dict[str, Any] = {}
        lineage: list[dict[str, Any]] = []

        try:
            from app.runtime.runtime_state_store import RuntimeStateStore

            store = RuntimeStateStore.for_execution(execution_id)
            kernel_state = {
                "actionCount": len(store._actions),
                "checkpointCount": len(store._checkpoints),
            }
            lineage = list(store._lineage)
        except Exception as exc:
            logger.debug("Kernel state capture partial: %s", exc)

        try:
            from app.execution.memory_runtime import MemoryRuntime

            mem = MemoryRuntime.get(execution_id)
            if mem:
                memory_state = mem.get_debug_snapshot()
        except Exception as exc:
            logger.debug("Memory state capture partial: %s", exc)

        return await StateSnapshotManager.capture(
            execution_id,
            graph_state=graph_state,
            memory_state=memory_state,
            kernel_state=kernel_state,
            lineage=lineage,
        )

    @staticmethod
    async def get_lineage(
        execution_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, snapshot_data, lineage_data, created_at
               FROM execution_lineage
               WHERE execution_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (execution_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "snapshotId": r["id"],
                "snapshot": json.loads(r["snapshot_data"] or "{}"),
                "lineage": json.loads(r["lineage_data"] or "[]"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
