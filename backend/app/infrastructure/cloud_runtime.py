"""
Cloud-native runtime hooks — K8s orchestration, tracing propagation, placement.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config.settings import settings
from app.infrastructure.deployment_profiles import DeploymentProfiles, DeploymentProfile

logger = logging.getLogger(__name__)


class CloudRuntime:
    """Kubernetes-native runtime integration hooks."""

    @staticmethod
    def enabled() -> bool:
        profile = DeploymentProfiles.detect_profile()
        return (
            settings.enable_autoscaling
            or profile in (DeploymentProfile.CLOUD_FEDERATED, DeploymentProfile.ENTERPRISE)
        )

    @staticmethod
    def get_runtime_context() -> dict[str, Any]:
        return {
            "podName": os.getenv("HOSTNAME", "local"),
            "namespace": os.getenv("K8S_NAMESPACE", "default"),
            "nodeName": os.getenv("K8S_NODE_NAME", settings.runtime_mesh_node_id),
            "deploymentProfile": DeploymentProfiles.detect_profile().value,
        }

    @staticmethod
    async def propagate_trace_context(execution_id: str) -> dict[str, str]:
        if not settings.enable_otel_tracing:
            return {}
        try:
            from app.infrastructure.distributed_tracing import DistributedTracing

            span = DistributedTracing.start_span(
                "cloud_execution",
                execution_id=execution_id,
            )
            return {"traceId": span.trace_id, "spanId": span.span_id}
        except Exception as exc:
            logger.debug("Trace propagation skipped: %s", exc)
            return {}

    @staticmethod
    def placement_policy() -> dict[str, Any]:
        return {
            "nodeAffinity": os.getenv("RUNTIME_NODE_AFFINITY", ""),
            "preferredZones": (os.getenv("RUNTIME_ZONES", "") or "").split(","),
            "maxPodsPerNode": int(os.getenv("MAX_PODS_PER_NODE", "4")),
        }
