"""
Predictive worker failure detection and autonomous recovery suggestions.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class PredictiveRecovery:
    """Predicts failures and suggests recovery actions."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_infra_intelligence

    @staticmethod
    async def evaluate_workers() -> list[dict[str, Any]]:
        if not PredictiveRecovery.enabled():
            return []
        predictions: list[dict[str, Any]] = []
        try:
            from app.infrastructure.worker_runtime import WorkerRuntime
            from app.infrastructure.worker_reputation import WorkerReputation

            workers = await WorkerRuntime.get_instance().list_workers(include_unhealthy=True)
            for w in workers:
                score = WorkerReputation.get_score(w.worker_id)
                load_ratio = w.active_jobs / max(w.max_concurrency, 1)
                if w.status != "healthy" or score < 0.5 or load_ratio > 0.9:
                    pred = {
                        "workerId": w.worker_id,
                        "risk": "high" if score < 0.5 else "medium",
                        "loadRatio": round(load_ratio, 2),
                        "reputation": score,
                        "suggestion": "drain_and_failover" if score < 0.5 else "reduce_load",
                    }
                    predictions.append(pred)
                    await emit_global_infrastructure_event(
                        "predictive_failure_detected",
                        f"Worker {w.worker_id} at risk",
                        **pred,
                    )
        except Exception as exc:
            logger.debug("Predictive evaluation skipped: %s", exc)
        return predictions

    @staticmethod
    async def trigger_self_healing() -> dict[str, Any]:
        if not PredictiveRecovery.enabled():
            return {"triggered": False}
        try:
            from app.infrastructure.failover_manager import FailoverManager
            from app.infrastructure.degraded_mode_manager import DegradedModeManager

            failover = await FailoverManager.run_failover_cycle()
            await emit_global_infrastructure_event(
                "self_healing_triggered",
                "Autonomous self-healing cycle executed",
                failover=failover,
                degraded=DegradedModeManager.status(),
            )
            return {"triggered": True, "failover": failover}
        except Exception as exc:
            logger.warning("Self-healing degraded: %s", exc)
            return {"triggered": False, "degraded": True}
