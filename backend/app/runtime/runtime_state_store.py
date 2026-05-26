"""
Unified runtime state store — authoritative execution state layer.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.runtime.action_system import ActionStatus, RuntimeAction

logger = logging.getLogger(__name__)


@dataclass
class ActionSnapshot:
    """Point-in-time snapshot of a single action."""

    action_id: str
    execution_id: str
    action_type: str
    status: str
    node_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    retry_lineage: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "executionId": self.execution_id,
            "actionType": self.action_type,
            "status": self.status,
            "nodeId": self.node_id,
            "payload": self.payload,
            "retryLineage": self.retry_lineage,
            "createdAt": self.created_at,
        }


@dataclass
class ExecutionSnapshot:
    """Full execution graph snapshot for replay and recovery."""

    execution_id: str
    workflow_id: str
    status: str
    action_snapshots: list[ActionSnapshot] = field(default_factory=list)
    graph_state: dict[str, Any] = field(default_factory=dict)
    memory_state: dict[str, Any] = field(default_factory=dict)
    cognition_state: dict[str, Any] = field(default_factory=dict)
    checkpoint_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpointId": self.checkpoint_id,
            "executionId": self.execution_id,
            "workflowId": self.workflow_id,
            "status": self.status,
            "actionCount": len(self.action_snapshots),
            "graphState": self.graph_state,
            "memoryState": self.memory_state,
            "cognitionState": self.cognition_state,
            "createdAt": self.created_at,
        }


@dataclass
class RuntimeCheckpoint:
    """Rollback checkpoint with recovery metadata."""

    checkpoint_id: str
    execution_id: str
    snapshot: ExecutionSnapshot
    reason: str = ""
    recovery_metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeStateStore:
    """
    SQLite-backed state store for execution snapshots and recovery lineage.
    """

    _instances: dict[str, RuntimeStateStore] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._actions: dict[str, ActionSnapshot] = {}
        self._checkpoints: list[RuntimeCheckpoint] = []
        self._lineage: list[dict[str, Any]] = []

    @classmethod
    def for_execution(cls, execution_id: str) -> RuntimeStateStore:
        if execution_id not in cls._instances:
            cls._instances[execution_id] = cls(execution_id)
        return cls._instances[execution_id]

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)

    async def record_action(self, action: RuntimeAction) -> None:
        snap = ActionSnapshot(
            action_id=action.id,
            execution_id=action.context.execution_id,
            action_type=action.action_type.value,
            status=action.status.value,
            node_id=action.context.node_id,
            payload=dict(action.payload),
            retry_lineage=list(action.provenance),
        )
        self._actions[action.id] = snap
        await self._persist_action_snapshot(snap)

    async def update_action_status(
        self,
        action_id: str,
        status: ActionStatus,
        *,
        retry_lineage: list[str] | None = None,
    ) -> None:
        snap = self._actions.get(action_id)
        if not snap:
            return
        snap.status = status.value
        if retry_lineage:
            snap.retry_lineage = retry_lineage
        await self._persist_action_snapshot(snap)

    async def save_checkpoint(
        self,
        workflow_id: str,
        status: str,
        *,
        graph_state: dict[str, Any] | None = None,
        memory_state: dict[str, Any] | None = None,
        cognition_state: dict[str, Any] | None = None,
        reason: str = "",
    ) -> RuntimeCheckpoint:
        snapshot = ExecutionSnapshot(
            execution_id=self.execution_id,
            workflow_id=workflow_id,
            status=status,
            action_snapshots=list(self._actions.values()),
            graph_state=graph_state or {},
            memory_state=memory_state or {},
            cognition_state=cognition_state or {},
        )
        checkpoint = RuntimeCheckpoint(
            checkpoint_id=snapshot.checkpoint_id,
            execution_id=self.execution_id,
            snapshot=snapshot,
            reason=reason,
        )
        self._checkpoints.append(checkpoint)
        await self._persist_execution_snapshot(snapshot)
        return checkpoint

    async def record_recovery_lineage(
        self,
        from_checkpoint: str,
        to_action: str,
        reason: str,
    ) -> None:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "executionId": self.execution_id,
            "fromCheckpoint": from_checkpoint,
            "toAction": to_action,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._lineage.append(entry)
        await self._persist_lineage(entry)

    def get_latest_checkpoint(self) -> RuntimeCheckpoint | None:
        return self._checkpoints[-1] if self._checkpoints else None

    def get_action_history(self) -> list[ActionSnapshot]:
        return list(self._actions.values())

    def replay_actions(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._actions.values()]

    async def _persist_action_snapshot(self, snap: ActionSnapshot) -> None:
        try:
            db = await get_db()
            await db.execute(
                """INSERT OR REPLACE INTO runtime_action_snapshots
                   (id, execution_id, action_type, status, node_id, payload,
                    retry_lineage, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snap.action_id,
                    snap.execution_id,
                    snap.action_type,
                    snap.status,
                    snap.node_id,
                    json.dumps(snap.payload),
                    json.dumps(snap.retry_lineage),
                    snap.created_at,
                ),
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to persist action snapshot %s", snap.action_id)

    async def _persist_execution_snapshot(self, snap: ExecutionSnapshot) -> None:
        try:
            db = await get_db()
            await db.execute(
                """INSERT INTO runtime_execution_snapshots
                   (id, execution_id, workflow_id, status, snapshot_data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    snap.checkpoint_id,
                    snap.execution_id,
                    snap.workflow_id,
                    snap.status,
                    json.dumps(snap.to_dict()),
                    snap.created_at,
                ),
            )
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to persist execution snapshot %s", snap.checkpoint_id
            )

    async def _persist_lineage(self, entry: dict[str, Any]) -> None:
        try:
            db = await get_db()
            await db.execute(
                """INSERT INTO runtime_recovery_lineage
                   (id, execution_id, from_checkpoint, to_action, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry["id"],
                    entry["executionId"],
                    entry["fromCheckpoint"],
                    entry["toAction"],
                    entry["reason"],
                    entry["timestamp"],
                ),
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to persist recovery lineage")

    def stats(self) -> dict[str, Any]:
        return {
            "actionCount": len(self._actions),
            "checkpointCount": len(self._checkpoints),
            "lineageCount": len(self._lineage),
        }
