"""
Cluster membership and discovery for federated runtime mesh.
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


class ClusterCoordinator:
    """Manages runtime cluster membership and node discovery."""

    _instance: ClusterCoordinator | None = None
    _local_cluster_id: str | None = None

    @classmethod
    def get_instance(cls) -> ClusterCoordinator:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return settings.enable_runtime_mesh or settings.enable_distributed_runtime

    async def ensure_local_cluster(self) -> str:
        if not self.enabled():
            return "local"
        try:
            if ClusterCoordinator._local_cluster_id:
                return ClusterCoordinator._local_cluster_id
            db = await get_db()
            cursor = await db.execute(
                "SELECT id FROM runtime_clusters WHERE status = 'active' LIMIT 1"
            )
            row = await cursor.fetchone()
            now = datetime.now(timezone.utc).isoformat()
            if row:
                ClusterCoordinator._local_cluster_id = row["id"]
            else:
                cid = f"cluster_{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO runtime_clusters
                       (id, name, topology, health_score, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (cid, "default", "{}", 1.0, "active", now, now),
                )
                await db.commit()
                ClusterCoordinator._local_cluster_id = cid
            await self.register_local_node(ClusterCoordinator._local_cluster_id)
            return ClusterCoordinator._local_cluster_id
        except Exception as exc:
            logger.warning("Cluster ensure degraded: %s", exc)
            return "local"

    async def register_local_node(self, cluster_id: str) -> dict[str, Any]:
        node_id = settings.runtime_mesh_node_id
        now = datetime.now(timezone.utc).isoformat()
        caps = ["execution", "tools", "browser", "coding"]
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id FROM cluster_nodes WHERE node_id = ?", (node_id,)
            )
            if await cursor.fetchone():
                await db.execute(
                    """UPDATE cluster_nodes SET last_heartbeat = ?, status = 'healthy',
                       updated_at = ? WHERE node_id = ?""",
                    (now, now, node_id),
                )
            else:
                nid = f"node_{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO cluster_nodes
                       (id, cluster_id, node_id, hostname, capabilities, affinity_tags,
                        health_score, status, last_heartbeat, metadata, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        nid,
                        cluster_id,
                        node_id,
                        "local",
                        json.dumps(caps),
                        json.dumps([]),
                        1.0,
                        "healthy",
                        now,
                        "{}",
                        now,
                        now,
                    ),
                )
                await emit_global_infrastructure_event(
                    "cluster_joined",
                    f"Node {node_id} joined cluster",
                    clusterId=cluster_id,
                    nodeId=node_id,
                )
            await db.commit()
            return {"clusterId": cluster_id, "nodeId": node_id, "status": "healthy"}
        except Exception as exc:
            logger.warning("Node registration degraded: %s", exc)
            return {"clusterId": cluster_id, "nodeId": node_id, "status": "degraded"}

    async def list_nodes(self, *, cluster_id: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled():
            return [
                {
                    "nodeId": settings.runtime_mesh_node_id,
                    "hostname": "local",
                    "status": "healthy",
                    "healthScore": 1.0,
                    "capabilities": ["execution"],
                }
            ]
        try:
            db = await get_db()
            if cluster_id:
                cursor = await db.execute(
                    "SELECT * FROM cluster_nodes WHERE cluster_id = ? ORDER BY updated_at DESC",
                    (cluster_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM cluster_nodes ORDER BY updated_at DESC LIMIT 50"
                )
            rows = await cursor.fetchall()
            return [
                {
                    "nodeId": r["node_id"],
                    "clusterId": r["cluster_id"],
                    "hostname": r["hostname"],
                    "status": r["status"],
                    "healthScore": r["health_score"],
                    "capabilities": json.loads(r["capabilities"] or "[]"),
                    "affinityTags": json.loads(r["affinity_tags"] or "[]"),
                    "lastHeartbeat": r["last_heartbeat"],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("List nodes degraded: %s", exc)
            return []

    async def check_node_health(self) -> list[str]:
        unhealthy: list[str] = []
        if not self.enabled():
            return unhealthy
        try:
            from app.infrastructure.worker_runtime import WorkerRuntime

            worker_unhealthy = await WorkerRuntime.get_instance().check_health()
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            for wid in worker_unhealthy:
                await db.execute(
                    "UPDATE cluster_nodes SET status = 'unhealthy', updated_at = ? WHERE node_id = ?",
                    (now, wid),
                )
                unhealthy.append(wid)
                await emit_global_infrastructure_event(
                    "cluster_node_unhealthy",
                    f"Node {wid} unhealthy",
                    nodeId=wid,
                )
            await db.commit()
        except Exception as exc:
            logger.debug("Node health check skipped: %s", exc)
        return unhealthy

    async def get_mesh_health_score(self) -> float:
        nodes = await self.list_nodes()
        if not nodes:
            return 1.0
        healthy = sum(1 for n in nodes if n.get("status") == "healthy")
        return round(healthy / len(nodes), 3)
