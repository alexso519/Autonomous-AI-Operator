"""
Scenario generator — self-generated benchmark tasks and edge-case scenarios.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)

_SCENARIO_TEMPLATES = [
    {
        "scenarioType": "retry_storm",
        "objective": "Execute task with simulated transient failures requiring bounded retries",
        "edgeCases": ["network_timeout", "tool_unavailable"],
        "confidence": 0.8,
    },
    {
        "scenarioType": "hallucination_stress",
        "objective": "Answer factual query requiring tool grounding under pressure",
        "edgeCases": ["missing_context", "ambiguous_query"],
        "confidence": 0.78,
    },
    {
        "scenarioType": "coordination_collapse",
        "objective": "Multi-agent task with conflicting sub-objectives",
        "edgeCases": ["agent_deadlock", "consensus_failure"],
        "confidence": 0.76,
    },
    {
        "scenarioType": "degraded_mode",
        "objective": "Complete objective under reduced capability set",
        "edgeCases": ["tool_disabled", "memory_limited"],
        "confidence": 0.77,
    },
]


class ScenarioGenerator:
    @classmethod
    async def generate_from_reflection(
        cls,
        meta_reflection: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        scenarios: list[dict[str, Any]] = []
        analysis = meta_reflection.get("analysis") or {}

        selected = list(_SCENARIO_TEMPLATES)
        if analysis.get("retryStorm"):
            selected = [s for s in selected if s["scenarioType"] == "retry_storm"] + selected[:1]
        if any("hallucination" in str(g) for g in meta_reflection.get("gaps") or []):
            selected.insert(0, _SCENARIO_TEMPLATES[1])

        for tmpl in selected[:3]:
            scenario_id = f"sg-{uuid.uuid4().hex[:12]}"
            scenario = {**tmpl, "id": scenario_id, "generated": True, "recommendationOnly": True}
            await MetaReasoningMemory.insert_row(
                "generated_scenarios",
                {
                    "id": scenario_id,
                    "scenario_type": tmpl["scenarioType"],
                    "scenario_data": json.dumps(scenario),
                    "confidence": tmpl["confidence"],
                    "created_at": now,
                },
            )
            scenarios.append(scenario)
        return scenarios
