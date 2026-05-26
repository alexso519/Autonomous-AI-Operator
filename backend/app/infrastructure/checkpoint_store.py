"""
Checkpoint store — integrates with RuntimeStateStore for replayable state.
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
class ExecutionCheckpoint:
    checkpoint_id: str
    execution_id: str
    label: str = ""
    snapshot_data: dict[str, Any] = field(default_factory=dict)
    action_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpointId": self.checkpoint_id,
            "executionId": self.execution_id,
            "label": self.label,
            "actionCount": self.action_count,
            "createdAt": self.created_at,
        }


class CheckpointStore:
    """Persistent checkpoint storage layered on RuntimeStateStore."""

    @staticmethod
    async def save(
        execution_id: str,
        snapshot_data: dict[str, Any],
        *,
        label: str = "",
        action_count: int = 0,
    ) -> ExecutionCheckpoint:
        checkpoint = ExecutionCheckpoint(
            checkpoint_id=uuid.uuid4().hex[:16],
            execution_id=execution_id,
            label=label,
            snapshot_data=snapshot_data,
            action_count=action_count,
        )
        db = await get_db()
        await db.execute(
            """INSERT INTO execution_snapshots
               (id, execution_id, label, snapshot_data, action_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                checkpoint.checkpoint_id,
                execution_id,
                label,
                json.dumps(snapshot_data),
                action_count,
                checkpoint.created_at,
            ),
        )
        await db.commit()

        await emit_infrastructure_event(
            execution_id,
            "execution_checkpoint_saved",
            f"Checkpoint saved: {label or checkpoint.checkpoint_id}",
            checkpointId=checkpoint.checkpoint_id,
            actionCount=action_count,
        )
        return checkpoint

    @staticmethod
    async def get_latest(execution_id: str) -> ExecutionCheckpoint | None:
        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM execution_snapshots
               WHERE execution_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return ExecutionCheckpoint(
            checkpoint_id=row["id"],
            execution_id=row["execution_id"],
            label=row["label"] or "",
            snapshot_data=json.loads(row["snapshot_data"] or "{}"),
            action_count=row["action_count"],
            created_at=row["created_at"],
        )

    @staticmethod
    async def list_checkpoints(
        execution_id: str, *, limit: int = 20
    ) -> list[ExecutionCheckpoint]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM execution_snapshots
               WHERE execution_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (execution_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            ExecutionCheckpoint(
                checkpoint_id=r["id"],
                execution_id=r["execution_id"],
                label=r["label"] or "",
                snapshot_data=json.loads(r["snapshot_data"] or "{}"),
                action_count=r["action_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    @staticmethod
    async def restore(execution_id: str, checkpoint_id: str) -> dict[str, Any] | None:
        db = await get_db()
        cursor = await db.execute(
            "SELECT snapshot_data FROM execution_snapshots WHERE id = ? AND execution_id = ?",
            (checkpoint_id, execution_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = json.loads(row["snapshot_data"] or "{}")
        await emit_infrastructure_event(
            execution_id,
            "execution_restored",
            f"Restored from checkpoint {checkpoint_id}",
            checkpointId=checkpoint_id,
        )
        return data
