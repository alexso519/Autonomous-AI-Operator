"""
Worker reputation scoring for infrastructure intelligence.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings
from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

logger = logging.getLogger(__name__)


class WorkerReputation:
    """Tracks and scores worker reliability."""

    _scores: dict[str, float] = {}
    _success: dict[str, int] = {}
    _failure: dict[str, int] = {}

    @staticmethod
    def enabled() -> bool:
        return settings.enable_infra_intelligence

    @staticmethod
    def record_outcome(worker_id: str, *, success: bool) -> float:
        if not WorkerReputation.enabled():
            return 1.0
        if success:
            WorkerReputation._success[worker_id] = WorkerReputation._success.get(worker_id, 0) + 1
        else:
            WorkerReputation._failure[worker_id] = WorkerReputation._failure.get(worker_id, 0) + 1
        s = WorkerReputation._success.get(worker_id, 0)
        f = WorkerReputation._failure.get(worker_id, 0)
        score = round(s / max(s + f, 1), 3)
        WorkerReputation._scores[worker_id] = score
        return score

    @staticmethod
    async def publish_scores() -> dict[str, float]:
        if not WorkerReputation.enabled():
            return {}
        try:
            from app.infrastructure.worker_runtime import WorkerRuntime

            workers = await WorkerRuntime.get_instance().list_workers(include_unhealthy=True)
            for w in workers:
                wid = w.worker_id
                if wid not in WorkerReputation._scores:
                    WorkerReputation._scores[wid] = 1.0 if w.status == "healthy" else 0.3
            await emit_global_infrastructure_event(
                "worker_reputation_updated",
                "Worker reputation scores updated",
                scores=WorkerReputation._scores,
            )
            return dict(WorkerReputation._scores)
        except Exception as exc:
            logger.debug("Reputation publish skipped: %s", exc)
            return dict(WorkerReputation._scores)

    @staticmethod
    def get_score(worker_id: str) -> float:
        return WorkerReputation._scores.get(worker_id, 1.0)

    @staticmethod
    def best_worker(worker_ids: list[str]) -> str | None:
        if not worker_ids:
            return None
        return max(worker_ids, key=lambda w: WorkerReputation.get_score(w))
