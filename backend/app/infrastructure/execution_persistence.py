"""
Durable workflow persistence for crash recovery and resume.
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
class PersistentExecution:
    execution_id: str
    workflow_id: str
    status: str = "running"
    checkpoint_data: dict[str, Any] = field(default_factory=dict)
    resume_token: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tenant_id: str = "default"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "workflowId": self.workflow_id,
            "status": self.status,
            "checkpointData": self.checkpoint_data,
            "resumeToken": self.resume_token,
            "tenantId": self.tenant_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class ExecutionPersistence:
    """SQLite-backed durable execution records."""

    @staticmethod
    async def create(
        execution_id: str,
        workflow_id: str,
        *,
        tenant_id: str = "default",
        checkpoint_data: dict[str, Any] | None = None,
    ) -> PersistentExecution:
        record = PersistentExecution(
            execution_id=execution_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            checkpoint_data=checkpoint_data or {},
        )
        db = await get_db()
        await db.execute(
            """INSERT INTO persistent_executions
               (id, workflow_id, status, checkpoint_data, resume_token,
                tenant_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.execution_id,
                record.workflow_id,
                record.status,
                json.dumps(record.checkpoint_data),
                record.resume_token,
                record.tenant_id,
                record.created_at,
                record.updated_at,
            ),
        )
        await db.commit()
        return record

    @staticmethod
    async def update_checkpoint(
        execution_id: str,
        checkpoint_data: dict[str, Any],
        *,
        status: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        if status:
            await db.execute(
                """UPDATE persistent_executions
                   SET checkpoint_data = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(checkpoint_data), status, now, execution_id),
            )
        else:
            await db.execute(
                """UPDATE persistent_executions
                   SET checkpoint_data = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(checkpoint_data), now, execution_id),
            )
        await db.commit()
        await emit_infrastructure_event(
            execution_id,
            "execution_checkpoint_saved",
            "Execution checkpoint persisted",
            checkpointSize=len(json.dumps(checkpoint_data)),
        )

    @staticmethod
    async def get(execution_id: str) -> PersistentExecution | None:
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM persistent_executions WHERE id = ?", (execution_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return PersistentExecution(
            execution_id=row["id"],
            workflow_id=row["workflow_id"],
            status=row["status"],
            checkpoint_data=json.loads(row["checkpoint_data"] or "{}"),
            resume_token=row["resume_token"],
            tenant_id=row["tenant_id"] or "default",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    async def list_resumable(*, limit: int = 50) -> list[PersistentExecution]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM persistent_executions
               WHERE status IN ('running', 'paused', 'recovering')
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            PersistentExecution(
                execution_id=r["id"],
                workflow_id=r["workflow_id"],
                status=r["status"],
                checkpoint_data=json.loads(r["checkpoint_data"] or "{}"),
                resume_token=r["resume_token"],
                tenant_id=r["tenant_id"] or "default",
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    @staticmethod
    async def mark_completed(execution_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            "UPDATE persistent_executions SET status = 'completed', updated_at = ? WHERE id = ?",
            (now, execution_id),
        )
        await db.commit()
