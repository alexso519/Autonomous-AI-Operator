"""
Execution recovery — resume workflows after crash or restart.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.infrastructure.checkpoint_store import CheckpointStore
from app.infrastructure.execution_persistence import ExecutionPersistence
from app.infrastructure.infrastructure_events import (
    emit_global_infrastructure_event,
    emit_infrastructure_event,
)

logger = logging.getLogger(__name__)


class ExecutionRecovery:
    """Crash recovery and checkpoint replay orchestrator."""

    @staticmethod
    async def recover_on_startup() -> dict[str, Any]:
        """Scan for interrupted executions and mark for recovery."""
        resumable = await ExecutionPersistence.list_resumable()
        recovered = 0
        for record in resumable:
            try:
                await ExecutionRecovery._record_event(
                    record.execution_id,
                    "startup_recovery_scan",
                    {"status": record.status},
                )
                recovered += 1
            except Exception as exc:
                logger.warning(
                    "Recovery scan failed for %s: %s", record.execution_id, exc
                )
        if recovered:
            await emit_global_infrastructure_event(
                "execution_recovered",
                f"Startup recovery scanned {recovered} execution(s)",
                count=recovered,
            )
        return {"scanned": len(resumable), "recovered": recovered}

    @staticmethod
    async def restore_execution(execution_id: str) -> dict[str, Any] | None:
        record = await ExecutionPersistence.get(execution_id)
        if not record:
            return None

        checkpoint = await CheckpointStore.get_latest(execution_id)
        snapshot = checkpoint.snapshot_data if checkpoint else record.checkpoint_data

        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            "UPDATE persistent_executions SET status = 'recovering', updated_at = ? WHERE id = ?",
            (now, execution_id),
        )
        await db.commit()

        await ExecutionRecovery._record_event(
            execution_id,
            "restore",
            {"checkpointId": checkpoint.checkpoint_id if checkpoint else None},
        )
        await emit_infrastructure_event(
            execution_id,
            "execution_recovered",
            "Execution state restored from persistence layer",
            resumeToken=record.resume_token,
        )
        return {
            "executionId": execution_id,
            "workflowId": record.workflow_id,
            "snapshot": snapshot,
            "resumeToken": record.resume_token,
        }

    @staticmethod
    async def get_recovery_timeline(
        execution_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM recovery_events
               WHERE execution_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (execution_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "eventId": r["id"],
                "executionId": r["execution_id"],
                "eventType": r["event_type"],
                "metadata": json.loads(r["metadata"] or "{}"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    @staticmethod
    async def _record_event(
        execution_id: str, event_type: str, metadata: dict[str, Any]
    ) -> None:
        db = await get_db()
        await db.execute(
            """INSERT INTO recovery_events
               (id, execution_id, event_type, metadata, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:16],
                execution_id,
                event_type,
                json.dumps(metadata),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
