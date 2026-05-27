"""
Evolution governor — approval thresholds, budget limits, quarantine orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.recursive_safety import RecursiveSafety

logger = logging.getLogger(__name__)


class EvolutionGovernor:
    """Gate capability evolution behind confidence + budget + safety checks."""

    @classmethod
    def is_meta_reflection_enabled(cls) -> bool:
        from app.config.settings import settings

        return settings.enable_self_improvement and settings.enable_meta_reflection

    @classmethod
    def is_capability_discovery_enabled(cls) -> bool:
        from app.config.settings import settings

        return settings.enable_self_improvement and settings.enable_capability_discovery

    @classmethod
    def is_adversarial_enabled(cls) -> bool:
        from app.config.settings import settings

        return settings.enable_self_improvement and settings.enable_adversarial_benchmarking

    @classmethod
    def is_coordination_evolution_enabled(cls) -> bool:
        from app.config.settings import settings

        return settings.enable_self_improvement and settings.enable_coordination_evolution

    @classmethod
    def is_predictive_runtime_enabled(cls) -> bool:
        from app.config.settings import settings

        return settings.enable_self_improvement and settings.enable_predictive_runtime

    @classmethod
    async def approve_proposal(cls, proposal: dict[str, Any]) -> dict[str, Any]:
        from app.config.settings import settings

        safety = await RecursiveSafety.preflight({}, proposed_actions=[proposal])
        confidence = float(proposal.get("confidence", 0.5))
        threshold = settings.self_improvement_confidence_threshold
        approved = safety["allowed"] and confidence >= threshold
        return {
            "approved": approved,
            "recommendationOnly": True,
            "confidence": confidence,
            "threshold": threshold,
            "safety": safety,
            "proposal": proposal,
        }

    @classmethod
    async def get_governance_snapshot(cls) -> dict[str, Any]:
        await MetaReasoningMemory.ensure_tables()
        return {
            "metaReflectionEnabled": cls.is_meta_reflection_enabled(),
            "capabilityDiscoveryEnabled": cls.is_capability_discovery_enabled(),
            "adversarialEnabled": cls.is_adversarial_enabled(),
            "coordinationEvolutionEnabled": cls.is_coordination_evolution_enabled(),
            "predictiveRuntimeEnabled": cls.is_predictive_runtime_enabled(),
            "killSwitchActive": RecursiveSafety.is_kill_switch_active(),
            "maxReflectionDepth": RecursiveSafety.max_reflection_depth(),
            "reflectionsToday": await MetaReasoningMemory.count_today("meta_reflections"),
            "governanceLog": await MetaReasoningMemory.query_recent("evolution_governance_log", limit=10),
        }
