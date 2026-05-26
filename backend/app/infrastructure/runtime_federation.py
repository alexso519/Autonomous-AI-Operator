"""
Runtime federation APIs — cross-cluster coordination facade.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RuntimeFederation:
    """Federation API for multi-cluster runtime coordination."""

    _instance: RuntimeFederation | None = None

    @classmethod
    def get_instance(cls) -> RuntimeFederation:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return settings.enable_runtime_mesh

    async def get_federation_status(self) -> dict[str, Any]:
        if not self.enabled():
            return {"federated": False, "mode": "local"}
        try:
            from app.infrastructure.runtime_mesh import RuntimeMesh
            from app.infrastructure.cloud_runtime import CloudRuntime

            topology = await RuntimeMesh.get_instance().get_topology()
            return {
                "federated": True,
                "mode": RuntimeMesh.mode(),
                "topology": topology,
                "cloud": CloudRuntime.get_runtime_context(),
            }
        except Exception as exc:
            logger.warning("Federation status degraded: %s", exc)
            return {"federated": False, "degraded": True}

    async def migrate_execution(
        self,
        execution_id: str,
        *,
        target_node: str,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"migrated": False, "reason": "federation_disabled"}
        try:
            from app.infrastructure.remote_execution_router import RemoteExecutionRouter

            replay = await RemoteExecutionRouter.replay_cross_node(execution_id)
            return {
                "migrated": True,
                "targetNode": target_node,
                "replay": replay,
            }
        except Exception as exc:
            logger.warning("Migration degraded: %s", exc)
            return {"migrated": False, "degraded": True}
