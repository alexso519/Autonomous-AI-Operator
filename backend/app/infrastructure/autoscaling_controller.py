"""
Autoscaling policies and adaptive worker scaling control.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class AutoscalingController:
    """Kubernetes-style autoscaling policy evaluation."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_autoscaling

    @staticmethod
    async def evaluate() -> dict[str, Any]:
        if not AutoscalingController.enabled():
            return {"scaling": "disabled"}
        try:
            from app.infrastructure.runtime_scaler import RuntimeScaler
            from app.infrastructure.distributed_queue import DistributedQueue

            scaling = await RuntimeScaler.evaluate_scaling()
            depth = await DistributedQueue.get_instance().queue_depth()
            pending = depth.get("pending", 0)
            recommendation = scaling.get("recommendation", "maintain")
            if pending > 50 and recommendation == "maintain":
                recommendation = "scale_up"
            elif pending < 5 and recommendation == "scale_up":
                recommendation = "maintain"

            result = {**scaling, "recommendation": recommendation, "queuePending": pending}
            if recommendation != "maintain":
                await emit_global_infrastructure_event(
                    "scaling_recommendation",
                    f"Autoscaling: {recommendation}",
                    **result,
                )
            return result
        except Exception as exc:
            logger.warning("Autoscaling evaluate degraded: %s", exc)
            return {"scaling": "degraded"}

    @staticmethod
    def hpa_config() -> dict[str, Any]:
        return {
            "minReplicas": 1,
            "maxReplicas": int(settings.max_worker_concurrency) * 2,
            "targetQueueDepth": 25,
            "targetCpuPercent": 70,
        }
