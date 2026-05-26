"""
Remote execution routing with capability-aware worker affinity.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)


class RemoteExecutionRouter:
    """Routes executions across federated runtime nodes."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_runtime_mesh

    @staticmethod
    def _shard_key(execution_id: str) -> str:
        return str(hash(execution_id) % 16)

    @staticmethod
    async def route_execution(
        execution_id: str,
        workflow_id: str,
        *,
        required_capabilities: list[str] | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        if not RemoteExecutionRouter.enabled():
            return {
                "mode": "local",
                "targetNode": settings.runtime_mesh_node_id,
                "executionId": execution_id,
            }
        try:
            from app.infrastructure.cluster_coordinator import ClusterCoordinator

            nodes = await ClusterCoordinator.get_instance().list_nodes()
            caps = required_capabilities or ["execution"]
            target = settings.runtime_mesh_node_id
            best_score = -1.0
            for node in nodes:
                if node.get("status") != "healthy":
                    continue
                node_caps = set(node.get("capabilities") or [])
                overlap = len(node_caps.intersection(caps))
                score = overlap * float(node.get("healthScore", 0.5))
                if score > best_score:
                    best_score = score
                    target = node["nodeId"]

            rid = f"rex_{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            shard = RemoteExecutionRouter._shard_key(execution_id)
            db = await get_db()
            await db.execute(
                """INSERT INTO remote_executions
                   (id, execution_id, source_node, target_node, shard_key, status,
                    replay_data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rid,
                    execution_id,
                    settings.runtime_mesh_node_id,
                    target,
                    shard,
                    "routed",
                    json.dumps({"workflowId": workflow_id, "tenantId": tenant_id}),
                    now,
                ),
            )
            await db.commit()
            await emit_infrastructure_event(
                execution_id,
                "remote_execution_started",
                f"Routed to node {target}",
                targetNode=target,
                shardKey=shard,
                remoteId=rid,
            )
            return {
                "mode": "remote",
                "remoteId": rid,
                "targetNode": target,
                "shardKey": shard,
                "executionId": execution_id,
            }
        except Exception as exc:
            logger.warning("Remote routing degraded to local: %s", exc)
            return {
                "mode": "local",
                "targetNode": settings.runtime_mesh_node_id,
                "executionId": execution_id,
                "degraded": True,
            }

    @staticmethod
    async def complete_remote(execution_id: str, *, status: str = "completed") -> None:
        if not RemoteExecutionRouter.enabled():
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            await db.execute(
                """UPDATE remote_executions SET status = ?, completed_at = ?
                   WHERE execution_id = ? AND status != 'completed'""",
                (status, now, execution_id),
            )
            await db.commit()
            await emit_infrastructure_event(
                execution_id,
                "remote_execution_completed",
                f"Remote execution {status}",
                status=status,
            )
        except Exception as exc:
            logger.debug("Remote complete skipped: %s", exc)

    @staticmethod
    async def replay_cross_node(execution_id: str) -> dict[str, Any] | None:
        if not RemoteExecutionRouter.enabled():
            return None
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT * FROM remote_executions WHERE execution_id = ? ORDER BY created_at DESC LIMIT 1",
                (execution_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "remoteId": row["id"],
                "sourceNode": row["source_node"],
                "targetNode": row["target_node"],
                "replayData": json.loads(row["replay_data"] or "{}"),
                "status": row["status"],
            }
        except Exception as exc:
            logger.debug("Cross-node replay skipped: %s", exc)
            return None
