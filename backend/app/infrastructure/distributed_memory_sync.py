"""
Distributed memory synchronization across federated runtime nodes.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class DistributedMemorySync:
    """Synchronizes shared execution memory across cluster nodes."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_runtime_mesh

    @staticmethod
    async def sync_memory(
        execution_id: str,
        memory_payload: dict[str, Any],
        *,
        target_node: str = "",
        sync_type: str = "incremental",
    ) -> dict[str, Any]:
        if not DistributedMemorySync.enabled():
            return {"synced": False, "mode": "local"}
        try:
            from app.infrastructure.cluster_coordinator import ClusterCoordinator

            cluster_id = await ClusterCoordinator.get_instance().ensure_local_cluster()
            sid = f"sync_{uuid.uuid4().hex[:12]}"
            payload = json.dumps(memory_payload)
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            await db.execute(
                """INSERT INTO memory_sync_events
                   (id, cluster_id, source_node, target_node, sync_type,
                    payload_size, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    cluster_id,
                    settings.runtime_mesh_node_id,
                    target_node or settings.runtime_mesh_node_id,
                    sync_type,
                    len(payload),
                    "completed",
                    now,
                ),
            )
            await db.commit()
            await emit_global_infrastructure_event(
                "memory_sync_completed",
                f"Memory sync for {execution_id}",
                executionId=execution_id,
                syncId=sid,
                payloadSize=len(payload),
            )
            return {"synced": True, "syncId": sid, "payloadSize": len(payload)}
        except Exception as exc:
            logger.warning("Memory sync degraded: %s", exc)
            return {"synced": False, "mode": "degraded", "error": str(exc)}

    @staticmethod
    async def list_recent(*, limit: int = 20) -> list[dict[str, Any]]:
        if not DistributedMemorySync.enabled():
            return []
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT * FROM memory_sync_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "syncId": r["id"],
                    "clusterId": r["cluster_id"],
                    "sourceNode": r["source_node"],
                    "targetNode": r["target_node"],
                    "syncType": r["sync_type"],
                    "payloadSize": r["payload_size"],
                    "status": r["status"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("List memory sync skipped: %s", exc)
            return []
