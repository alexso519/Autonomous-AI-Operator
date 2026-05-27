"""
Agent architect — recommend new agent archetypes from execution patterns.
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

_ARCHETYPE_TEMPLATES = {
    "research_specialist": {
        "role": "research_specialist",
        "capabilities": ["web_search", "citation_grounding", "evidence_synthesis"],
        "description": "Deep research with grounded citations",
    },
    "tool_orchestrator": {
        "role": "tool_orchestrator",
        "capabilities": ["tool_selection", "parallel_invocation", "result_aggregation"],
        "description": "Optimizes tool chains and parallel tool use",
    },
    "recovery_agent": {
        "role": "recovery_agent",
        "capabilities": ["failure_classification", "retry_strategy", "degraded_mode"],
        "description": "Handles failure recovery and retry coordination",
    },
}


class AgentArchitect:
    @classmethod
    async def propose_archetypes(
        cls,
        ctx_snapshot: dict[str, Any],
        gaps: list[dict[str, Any]],
        analysis: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        analysis = analysis or {}

        gap_keys = {g.get("key", "") for g in gaps}
        if any("tool_grounding" in k or "hallucination" in k for k in gap_keys):
            proposals.append(cls._proposal("research_specialist", 0.78))
        if analysis.get("retryStorm"):
            proposals.append(cls._proposal("recovery_agent", 0.84))
        if analysis.get("coordinationScore", 1.0) < 0.55:
            proposals.append(cls._proposal("tool_orchestrator", 0.76))

        tool_calls = ctx_snapshot.get("tool_calls") or []
        if len(tool_calls) > 5:
            proposals.append(cls._proposal("tool_orchestrator", 0.72))

        persisted: list[dict[str, Any]] = []
        for prop in proposals:
            prop = AutonomyBoundaries.sanitize_proposal(prop)
            prop_id = f"aa-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "agent_archetype_proposals",
                {
                    "id": prop_id,
                    "archetype_name": prop["archetypeName"],
                    "proposal_data": json.dumps(prop),
                    "confidence": prop["confidence"],
                    "created_at": now,
                },
            )
            persisted.append({**prop, "id": prop_id})
        return persisted

    @classmethod
    def _proposal(cls, name: str, confidence: float) -> dict[str, Any]:
        template = _ARCHETYPE_TEMPLATES.get(name, {"role": name, "capabilities": [], "description": name})
        return {
            "type": "agent_archetype_proposal",
            "archetypeName": name,
            "definition": template,
            "confidence": confidence,
            "recommendationOnly": True,
        }
