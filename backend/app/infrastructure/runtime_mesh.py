"""
Federated runtime mesh — topology, health, and hybrid federation modes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class RuntimeMesh:
    """Facade for multi-runtime federation and mesh health."""

    _instance: RuntimeMesh | None = None

    @classmethod
    def get_instance(cls) -> RuntimeMesh:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return settings.enable_runtime_mesh

    @staticmethod
    def mode() -> str:
        if not settings.enable_runtime_mesh:
            return "local"
        if settings.enable_distributed_runtime and settings.redis_url:
            return "hybrid"
        if settings.enable_distributed_runtime:
            return "distributed_cluster"
        return "federation"

    async def initialize(self) -> dict[str, Any]:
        if not self.enabled():
            return {"mode": "local", "nodes": 1}
        try:
            from app.infrastructure.cluster_coordinator import ClusterCoordinator

            cluster_id = await ClusterCoordinator.get_instance().ensure_local_cluster()
            topology = await self.get_topology()
            await emit_global_infrastructure_event(
                "runtime_mesh_updated",
                "Runtime mesh initialized",
                mode=self.mode(),
                clusterId=cluster_id,
                nodeCount=len(topology.get("nodes", [])),
            )
            return {"mode": self.mode(), "clusterId": cluster_id, "topology": topology}
        except Exception as exc:
            logger.warning("Runtime mesh init degraded: %s", exc)
            return {"mode": "local", "degraded": True}

    async def get_topology(self) -> dict[str, Any]:
        if not self.enabled():
            return {
                "mode": "local",
                "healthScore": 1.0,
                "nodes": [
                    {
                        "nodeId": settings.runtime_mesh_node_id,
                        "status": "healthy",
                        "role": "primary",
                    }
                ],
            }
        try:
            from app.infrastructure.cluster_coordinator import ClusterCoordinator

            coord = ClusterCoordinator.get_instance()
            nodes = await coord.list_nodes()
            health = await coord.get_mesh_health_score()
            return {
                "mode": self.mode(),
                "healthScore": health,
                "localNodeId": settings.runtime_mesh_node_id,
                "nodes": nodes,
            }
        except Exception as exc:
            logger.warning("Topology degraded: %s", exc)
            return {"mode": "local", "healthScore": 0.5, "nodes": [], "degraded": True}

    async def on_execution_start(
        self,
        execution_id: str,
        workflow_id: str,
        *,
        capabilities: list[str] | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"routed": False}
        try:
            from app.infrastructure.remote_execution_router import RemoteExecutionRouter

            return await RemoteExecutionRouter.route_execution(
                execution_id,
                workflow_id,
                required_capabilities=capabilities,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.debug("Mesh execution start skipped: %s", exc)
            return {"routed": False}

    async def on_execution_complete(self, execution_id: str, *, status: str = "completed") -> None:
        if not self.enabled():
            return
        try:
            from app.infrastructure.remote_execution_router import RemoteExecutionRouter

            await RemoteExecutionRouter.complete_remote(execution_id, status=status)
            topology = await self.get_topology()
            await emit_global_infrastructure_event(
                "runtime_mesh_updated",
                "Mesh topology refreshed",
                healthScore=topology.get("healthScore", 1.0),
            )
        except Exception as exc:
            logger.debug("Mesh execution complete skipped: %s", exc)

    async def maintenance_cycle(self) -> dict[str, Any]:
        if not self.enabled():
            return {}
        try:
            from app.infrastructure.cluster_coordinator import ClusterCoordinator

            unhealthy = await ClusterCoordinator.get_instance().check_node_health()
            topology = await self.get_topology()
            return {"unhealthy": unhealthy, "topology": topology}
        except Exception as exc:
            logger.debug("Mesh maintenance skipped: %s", exc)
            return {}
