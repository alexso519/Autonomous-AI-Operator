"""
Evolution guardrails — disable evolution during instability, enforce cooldowns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.safety_limits import SafetyLimits, is_safe_optimization, limits_from_settings

logger = logging.getLogger(__name__)

_last_cycle_at: datetime | None = None


class EvolutionGuardrails:
    @classmethod
    def check_runtime_stable(cls, ctx_snapshot: dict[str, Any]) -> tuple[bool, str]:
        """Block evolution when execution signals instability."""
        retries = int(ctx_snapshot.get("global_retry_count") or 0)
        if retries > 8:
            return False, "excessive_retries"
        reflections = ctx_snapshot.get("reflection_history") or []
        if len(reflections) > 12:
            return False, "reflection_storm"
        health = ctx_snapshot.get("runtime_health") or {}
        if health.get("degraded") and health.get("severity") == "critical":
            return False, "runtime_degraded"
        return True, "ok"

    @classmethod
    def check_cooldown(cls, limits: SafetyLimits | None = None) -> tuple[bool, str]:
        global _last_cycle_at
        limits = limits or limits_from_settings()
        if _last_cycle_at is None:
            return True, "ok"
        elapsed = (datetime.now(timezone.utc) - _last_cycle_at).total_seconds()
        if elapsed < limits.optimization_cooldown_seconds:
            return False, "cooldown_active"
        return True, "ok"

    @classmethod
    def mark_cycle_started(cls) -> None:
        global _last_cycle_at
        _last_cycle_at = datetime.now(timezone.utc)

    @classmethod
    def validate_action(cls, action: dict[str, Any]) -> tuple[bool, str]:
        if not is_safe_optimization(action):
            return False, "unsafe_action_type"
        return True, "ok"

    @classmethod
    def reset_cooldown_for_tests(cls) -> None:
        global _last_cycle_at
        _last_cycle_at = None
