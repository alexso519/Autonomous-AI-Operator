"""
Recursive safety layer — depth limits, kill switch, quarantine, rollback enforcement.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.autonomy_boundaries import AutonomyBoundaries
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)

_quarantine_active = False
_instability_freeze = False
_anomaly_spike_count = 0


class RecursiveSafety:
    @classmethod
    def is_kill_switch_active(cls) -> bool:
        from app.config.settings import settings

        return bool(getattr(settings, "self_evolution_kill_switch", False))

    @classmethod
    def max_reflection_depth(cls) -> int:
        from app.config.settings import settings

        return int(getattr(settings, "max_recursive_reflection_depth", 2))

    @classmethod
    def unsafe_emergence_threshold(cls) -> float:
        from app.config.settings import settings

        return float(getattr(settings, "unsafe_emergence_threshold", 0.92))

    @classmethod
    async def preflight(
        cls,
        ctx_snapshot: dict[str, Any],
        *,
        depth: int = 0,
        proposed_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from app.config.settings import settings

        proposed_actions = proposed_actions or []
        blocked_reasons: list[str] = []

        if cls.is_kill_switch_active():
            blocked_reasons.append("kill_switch_active")
        if _quarantine_active:
            blocked_reasons.append("quarantine_mode")
        if _instability_freeze:
            blocked_reasons.append("instability_freeze")

        if depth > cls.max_reflection_depth():
            blocked_reasons.append("max_reflection_depth_exceeded")

        cycles_today = await MetaReasoningMemory.count_today("meta_reflections")
        max_cycles = int(getattr(settings, "max_autonomous_evolution_cycles_per_day", 20))
        if cycles_today >= max_cycles:
            blocked_reasons.append("daily_evolution_budget_exhausted")

        retries = int(ctx_snapshot.get("global_retry_count") or 0)
        if retries > 10:
            blocked_reasons.append("retry_storm_active")

        rejected: list[dict[str, Any]] = []
        for action in proposed_actions:
            ok, reason = AutonomyBoundaries.is_within_boundaries(action)
            if not ok:
                rejected.append({**action, "rejectReason": reason})
            elif action.get("confidence", 0.5) < settings.self_improvement_confidence_threshold:
                rejected.append({**action, "rejectReason": "low_confidence"})

        emergence_score = cls._compute_emergence_score(ctx_snapshot, proposed_actions)
        if emergence_score >= cls.unsafe_emergence_threshold():
            blocked_reasons.append("unsafe_emergence_detected")
            cls._mark_anomaly_spike()

        allowed = len(blocked_reasons) == 0 and len(rejected) == 0
        result = {
            "allowed": allowed,
            "blockedReasons": blocked_reasons,
            "rejectedActions": rejected,
            "emergenceScore": emergence_score,
            "depth": depth,
            "quarantineActive": _quarantine_active,
            "instabilityFreeze": _instability_freeze,
            "killSwitchActive": cls.is_kill_switch_active(),
        }
        await cls._log_governance("preflight", result, blocked=not allowed)
        return result

    @classmethod
    def _compute_emergence_score(
        cls, ctx: dict[str, Any], actions: list[dict[str, Any]]
    ) -> float:
        score = 0.0
        reflections = ctx.get("reflection_history") or []
        score += min(0.3, len(reflections) * 0.03)
        score += min(0.2, int(ctx.get("global_retry_count") or 0) * 0.02)
        score += min(0.3, len(actions) * 0.05)
        health = ctx.get("runtime_health") or {}
        if health.get("degraded"):
            score += 0.15
        return min(1.0, score)

    @classmethod
    def _mark_anomaly_spike(cls) -> None:
        global _anomaly_spike_count, _instability_freeze
        _anomaly_spike_count += 1
        if _anomaly_spike_count >= 3:
            _instability_freeze = True
            logger.warning("Instability freeze activated after anomaly spike")

    @classmethod
    async def enforce_rollback(cls, session_id: str, reason: str, data: dict[str, Any]) -> str:
        from app.self_improvement.improvement_safety import ImprovementSafety

        rollback_id = await ImprovementSafety.record_rollback(session_id, reason, data)
        global _quarantine_active
        _quarantine_active = True
        await cls._log_governance("rollback_enforced", {"sessionId": session_id, "reason": reason}, blocked=True)
        return rollback_id

    @classmethod
    async def _log_governance(cls, event_type: str, event_data: dict[str, Any], *, blocked: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await MetaReasoningMemory.insert_row(
            "evolution_governance_log",
            {
                "id": f"gov-{uuid.uuid4().hex[:12]}",
                "event_type": event_type,
                "event_data": json.dumps(event_data),
                "blocked": 1 if blocked else 0,
                "created_at": now,
            },
        )

    @classmethod
    def reset_for_tests(cls) -> None:
        global _quarantine_active, _instability_freeze, _anomaly_spike_count
        _quarantine_active = False
        _instability_freeze = False
        _anomaly_spike_count = 0
        MetaReasoningMemory.reset_for_tests()
