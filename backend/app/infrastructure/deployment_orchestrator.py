"""
Deployment rollout coordination and rolling execution migration.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class DeploymentOrchestrator:
    """Coordinates deployment rollouts and worker drain."""

    _draining: set[str] = set()

    @staticmethod
    def enabled() -> bool:
        return settings.enable_autoscaling or settings.enable_distributed_runtime

    @staticmethod
    async def start_rollout(*, version: str = "") -> dict[str, Any]:
        if not DeploymentOrchestrator.enabled():
            return {"status": "local", "rollout": False}
        return {
            "status": "in_progress",
            "version": version or "current",
            "strategy": "rolling",
        }

    @staticmethod
    async def drain_worker(worker_id: str) -> dict[str, Any]:
        if not DeploymentOrchestrator.enabled():
            return {"drained": False}
        try:
            from app.infrastructure.failover_manager import FailoverManager

            DeploymentOrchestrator._draining.add(worker_id)
            result = await FailoverManager.drain_worker(worker_id)
            return {"drained": True, **result}
        except Exception as exc:
            logger.warning("Worker drain degraded: %s", exc)
            return {"drained": False, "degraded": True}

    @staticmethod
    async def coordinate_migration(execution_ids: list[str]) -> dict[str, Any]:
        migrated = 0
        if not settings.enable_runtime_mesh:
            return {"migrated": 0, "total": len(execution_ids)}
        try:
            from app.infrastructure.runtime_federation import RuntimeFederation

            fed = RuntimeFederation.get_instance()
            for eid in execution_ids:
                result = await fed.migrate_execution(eid, target_node="pending")
                if result.get("migrated"):
                    migrated += 1
        except Exception as exc:
            logger.debug("Migration coordination skipped: %s", exc)
        return {"migrated": migrated, "total": len(execution_ids)}
