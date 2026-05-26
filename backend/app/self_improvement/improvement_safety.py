"""
Safety gate before applying any optimization — audit + rollback readiness.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.evolution_guardrails import EvolutionGuardrails
from app.self_improvement.evolution_policy import EvolutionPolicy
from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.safety_limits import SafetyLimits, limits_from_settings

logger = logging.getLogger(__name__)


class ImprovementSafety:
    @classmethod
    async def preflight(
        cls,
        ctx_snapshot: dict[str, Any],
        proposed_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        limits = limits_from_settings()
        policy = await EvolutionPolicy.get_active_policy()
        policy = EvolutionPolicy.merge_with_limits(policy, limits)

        stable, stable_reason = EvolutionGuardrails.check_runtime_stable(ctx_snapshot)
        cooldown_ok, cooldown_reason = EvolutionGuardrails.check_cooldown(limits)

        safe_actions: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for action in proposed_actions:
            ok, reason = EvolutionGuardrails.validate_action(action)
            if ok and action.get("confidence", 0.5) >= limits.confidence_threshold:
                safe_actions.append(action)
            else:
                rejected.append({**action, "rejectReason": reason or "low_confidence"})

        mutations_today = await ImprovementMemoryStore.count_today("strategy_mutations")
        mutation_cap = mutations_today < limits.max_heuristic_mutations_per_day

        return {
            "allowed": stable and cooldown_ok and mutation_cap and len(safe_actions) > 0,
            "dryRun": policy.get("dryRun", True),
            "stable": stable,
            "stableReason": stable_reason,
            "cooldownOk": cooldown_ok,
            "cooldownReason": cooldown_reason,
            "mutationCapOk": mutation_cap,
            "safeActions": safe_actions,
            "rejectedActions": rejected,
            "policy": policy,
        }

    @classmethod
    async def audit(
        cls,
        session_id: str,
        action_type: str,
        audit_data: dict[str, Any],
        *,
        replayable: bool = True,
    ) -> str:
        audit_id = f"aud-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await ImprovementMemoryStore.insert_row(
            "improvement_audits",
            {
                "id": audit_id,
                "session_id": session_id,
                "action_type": action_type,
                "audit_data": json.dumps(audit_data),
                "replayable": 1 if replayable else 0,
                "created_at": now,
            },
        )
        return audit_id

    @classmethod
    async def record_rollback(
        cls,
        session_id: str,
        trigger_reason: str,
        rollback_data: dict[str, Any],
    ) -> str:
        rollback_id = f"rb-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await ImprovementMemoryStore.insert_row(
            "optimization_rollbacks",
            {
                "id": rollback_id,
                "session_id": session_id,
                "rollback_data": json.dumps(rollback_data),
                "trigger_reason": trigger_reason,
                "created_at": now,
            },
        )
        return rollback_id
