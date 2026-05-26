"""
Disaster recovery snapshots and execution export/import.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class DisasterRecovery:
    """Backup, restore, and emergency recovery operations."""

    @staticmethod
    async def create_snapshot(label: str = "manual") -> dict[str, Any]:
        try:
            from app.database.database import get_db

            db = await get_db()
            snapshot: dict[str, Any] = {
                "label": label,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "executions": [],
                "workers": [],
            }
            for table, key in [
                ("persistent_executions", "executions"),
                ("worker_nodes", "workers"),
            ]:
                try:
                    cursor = await db.execute(f"SELECT * FROM {table} LIMIT 100")
                    rows = await cursor.fetchall()
                    snapshot[key] = [dict(r) for r in rows]
                except Exception:
                    pass
            sid = f"dr_{uuid.uuid4().hex[:12]}"
            return {"snapshotId": sid, "snapshot": snapshot, "recordCount": len(snapshot.get("executions", []))}
        except Exception as exc:
            logger.warning("DR snapshot degraded: %s", exc)
            return {"snapshotId": None, "degraded": True}

    @staticmethod
    async def export_execution(execution_id: str) -> dict[str, Any] | None:
        try:
            from app.infrastructure.execution_persistence import ExecutionPersistence
            from app.infrastructure.checkpoint_store import CheckpointStore

            record = await ExecutionPersistence.get(execution_id)
            if not record:
                return None
            checkpoints = await CheckpointStore.list_checkpoints(execution_id)
            return {
                "executionId": execution_id,
                "record": record.to_dict(),
                "checkpoints": [c.to_dict() for c in checkpoints],
            }
        except Exception as exc:
            logger.warning("Export degraded: %s", exc)
            return None

    @staticmethod
    async def import_execution(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from app.infrastructure.execution_persistence import ExecutionPersistence

            record = payload.get("record") or {}
            eid = record.get("executionId") or record.get("execution_id")
            wf = record.get("workflowId") or record.get("workflow_id", "")
            if not eid:
                return {"imported": False, "reason": "missing_execution_id"}
            existing = await ExecutionPersistence.get(eid)
            if not existing:
                await ExecutionPersistence.create(
                    eid,
                    wf,
                    tenant_id=record.get("tenantId", "default"),
                    checkpoint_data=record.get("checkpointData", {}),
                )
            return {"imported": True, "executionId": eid}
        except Exception as exc:
            logger.warning("Import degraded: %s", exc)
            return {"imported": False, "degraded": True}

    @staticmethod
    async def emergency_mode() -> dict[str, Any]:
        try:
            from app.infrastructure.degraded_mode_manager import DegradedModeManager

            await DegradedModeManager.enable("emergency_recovery")
            return {"emergencyMode": True, "degraded": DegradedModeManager.status()}
        except Exception as exc:
            return {"emergencyMode": False, "error": str(exc)}
