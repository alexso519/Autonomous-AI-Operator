"""
Stress evolution — retry-storm, coordination collapse, resilience scoring.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.failure_simulator import FailureSimulator
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class StressEvolution:
    @classmethod
    async def run_stress_profile(cls, scenario: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        scenario_type = scenario.get("scenarioType", "generic")
        simulation = FailureSimulator.simulate(scenario_type)

        resilience = cls._score_resilience(simulation, scenario)
        profile_id = f"sp-{uuid.uuid4().hex[:12]}"
        profile = {
            "scenarioId": scenario.get("id"),
            "simulation": simulation,
            "resilienceScore": resilience,
            "profileType": scenario_type,
        }
        await MetaReasoningMemory.insert_row(
            "stress_profiles",
            {
                "id": profile_id,
                "profile_type": scenario_type,
                "profile_data": json.dumps(profile),
                "severity": 1.0 - resilience,
                "created_at": now,
            },
        )
        score_id = f"rs-{uuid.uuid4().hex[:12]}"
        await MetaReasoningMemory.insert_row(
            "resilience_scores",
            {
                "id": score_id,
                "metric": scenario_type,
                "score": resilience,
                "score_data": json.dumps(profile),
                "created_at": now,
            },
        )
        return {**profile, "profileId": profile_id, "scoreId": score_id}

    @classmethod
    def _score_resilience(cls, simulation: dict[str, Any], scenario: dict[str, Any]) -> float:
        base = 0.65
        if simulation.get("expectedBehavior"):
            base += 0.1
        if simulation.get("fallbackTools"):
            base += 0.08
        conf = float(scenario.get("confidence") or simulation.get("confidence") or 0.7)
        return min(1.0, max(0.2, base + (conf - 0.7) * 0.2))

    @classmethod
    async def get_resilience_trends(cls, limit: int = 30) -> dict[str, Any]:
        return {
            "scores": await MetaReasoningMemory.query_recent("resilience_scores", limit=limit),
            "profiles": await MetaReasoningMemory.query_recent("stress_profiles", limit=limit),
        }
