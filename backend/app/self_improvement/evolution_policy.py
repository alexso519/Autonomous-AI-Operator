"""
Evolution policy — max mutation rate, drift, rollback triggers, dry-run mode.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.self_improvement.safety_limits import SafetyLimits, limits_from_settings

logger = logging.getLogger(__name__)

DEFAULT_POLICY: dict[str, Any] = {
    "maxMutationRate": 0.25,
    "maxParameterDrift": 0.15,
    "benchmarkSafetyThreshold": 0.70,
    "rollbackTriggerThreshold": 0.60,
    "optimizationCooldownSeconds": 300,
    "dryRun": True,
}


class EvolutionPolicy:
    @classmethod
    async def ensure_tables(cls) -> None:
        from app.self_improvement.improvement_memory import ImprovementMemoryStore

        await ImprovementMemoryStore.ensure_tables()

    @classmethod
    async def get_active_policy(cls) -> dict[str, Any]:
        await cls.ensure_tables()
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT policy_data FROM evolution_policies
                   ORDER BY updated_at DESC LIMIT 1"""
            )
            row = await cursor.fetchone()
            if row:
                return {**DEFAULT_POLICY, **json.loads(row["policy_data"])}
        except Exception as exc:
            logger.debug("Policy load failed: %s", exc)
        limits = limits_from_settings()
        from app.config.settings import settings

        return {
            **DEFAULT_POLICY,
            "maxMutationRate": limits.max_mutation_rate,
            "maxParameterDrift": limits.max_parameter_drift,
            "benchmarkSafetyThreshold": limits.benchmark_safety_threshold,
            "rollbackTriggerThreshold": limits.rollback_trigger_threshold,
            "optimizationCooldownSeconds": limits.optimization_cooldown_seconds,
            "dryRun": settings.self_improvement_dry_run,
            "confidenceThreshold": limits.confidence_threshold,
        }

    @classmethod
    async def save_policy(cls, policy: dict[str, Any], reason: str = "") -> str:
        await cls.ensure_tables()
        policy_id = f"pol-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            """INSERT INTO evolution_policies (id, policy_data, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (policy_id, json.dumps(policy), reason, now, now),
        )
        await db.commit()
        return policy_id

    @classmethod
    def merge_with_limits(cls, policy: dict[str, Any], limits: SafetyLimits) -> dict[str, Any]:
        return {
            **policy,
            "maxMutationRate": min(policy.get("maxMutationRate", 0.25), limits.max_mutation_rate),
            "maxParameterDrift": min(
                policy.get("maxParameterDrift", 0.15), limits.max_parameter_drift
            ),
        }
