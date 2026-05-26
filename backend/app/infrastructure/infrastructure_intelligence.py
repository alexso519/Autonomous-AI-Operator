"""
Autonomous infrastructure intelligence — integrates diagnostics, scaling, failover.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class InfrastructureIntelligence:
    """Central intelligence facade for predictive ops and reliability scoring."""

    _instance: InfrastructureIntelligence | None = None
    _reliability_score: float = 1.0

    @classmethod
    def get_instance(cls) -> InfrastructureIntelligence:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return settings.enable_infra_intelligence

    async def maintenance_cycle(self) -> dict[str, Any]:
        if not self.enabled():
            return {"enabled": False}
        result: dict[str, Any] = {"enabled": True}
        try:
            from app.infrastructure.anomaly_detector import AnomalyDetector
            from app.infrastructure.predictive_recovery import PredictiveRecovery
            from app.infrastructure.worker_reputation import WorkerReputation
            from app.infrastructure.runtime_scaler import RuntimeScaler
            from app.infrastructure.distributed_queue import DistributedQueue

            result["anomalies"] = await AnomalyDetector.scan_metrics()
            result["predictions"] = await PredictiveRecovery.evaluate_workers()
            result["reputation"] = await WorkerReputation.publish_scores()

            depth = await DistributedQueue.get_instance().queue_depth()
            scaling = await RuntimeScaler.evaluate_scaling()
            result["scaling"] = scaling
            result["queueDepth"] = depth

            healthy_workers = sum(
                1 for _, s in (result.get("reputation") or {}).items() if s >= 0.7
            )
            total_workers = max(len(result.get("reputation") or {}), 1)
            pending = depth.get("pending", 0)
            anomaly_penalty = 0.1 * len(result.get("anomalies") or [])
            load_penalty = min(0.3, pending / 500)
            worker_factor = healthy_workers / total_workers
            InfrastructureIntelligence._reliability_score = round(
                max(0.1, worker_factor - anomaly_penalty - load_penalty), 3
            )

            await emit_global_infrastructure_event(
                "runtime_reliability_updated",
                f"Reliability score: {InfrastructureIntelligence._reliability_score}",
                score=InfrastructureIntelligence._reliability_score,
            )
            result["reliabilityScore"] = InfrastructureIntelligence._reliability_score

            if (result.get("anomalies") or result.get("predictions")) and pending > 20:
                result["selfHealing"] = await PredictiveRecovery.trigger_self_healing()
        except Exception as exc:
            logger.warning("Intelligence cycle degraded: %s", exc)
            result["degraded"] = True
        return result

    async def on_execution_retry(self, execution_id: str) -> None:
        if not self.enabled():
            return
        try:
            from app.infrastructure.anomaly_detector import AnomalyDetector

            await AnomalyDetector.record_retry(execution_id)
        except Exception as exc:
            logger.debug("Retry anomaly hook skipped: %s", exc)

    async def get_execution_diagnostics(self, execution_id: str) -> dict[str, Any]:
        diag: dict[str, Any] = {}
        try:
            from app.runtime.runtime_diagnostics import RuntimeDiagnostics

            d = RuntimeDiagnostics.for_execution(execution_id)
            diag = d.snapshot()
        except Exception:
            pass
        return {
            "executionId": execution_id,
            "diagnostics": diag,
            "reliabilityScore": InfrastructureIntelligence._reliability_score,
        }

    async def get_dashboard(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "reliabilityScore": InfrastructureIntelligence._reliability_score,
        }

    @staticmethod
    async def adaptive_queue_balance() -> dict[str, Any]:
        if not InfrastructureIntelligence.enabled():
            return {}
        try:
            from app.infrastructure.runtime_scaler import RuntimeScaler

            return await RuntimeScaler.evaluate_scaling()
        except Exception as exc:
            logger.debug("Queue balance skipped: %s", exc)
            return {}
