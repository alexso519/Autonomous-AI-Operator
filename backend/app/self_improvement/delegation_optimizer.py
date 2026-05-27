"""
Delegation optimizer — communication-overhead reduction, adaptive arbitration.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class DelegationOptimizer:
    @classmethod
    async def optimize(
        cls,
        ctx_snapshot: dict[str, Any],
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        agents = ctx_snapshot.get("active_agents") or ctx_snapshot.get("spawned_agents") or []
        agent_count = len(agents) if isinstance(agents, list) else 0
        coordination = analysis.get("coordinationScore", 0.7)

        strategy = "flat_delegation"
        effectiveness = 0.7
        if agent_count > 4 and coordination < 0.6:
            strategy = "hierarchical_delegation"
            effectiveness = 0.78
        elif agent_count <= 2:
            strategy = "direct_assignment"
            effectiveness = 0.82
        elif analysis.get("retryStorm"):
            strategy = "single_owner_with_fallback"
            effectiveness = 0.8

        profile = {
            "strategy": strategy,
            "agentCount": agent_count,
            "coordinationScore": coordination,
            "communicationReduction": "batch_updates" if agent_count > 3 else "direct",
            "recommendationOnly": True,
            "confidence": effectiveness,
        }

        profile_id = f"dp-{uuid.uuid4().hex[:12]}"
        await MetaReasoningMemory.insert_row(
            "delegation_profiles",
            {
                "id": profile_id,
                "profile_key": strategy,
                "profile_data": json.dumps(profile),
                "effectiveness": effectiveness,
                "created_at": now,
            },
        )
        return {**profile, "id": profile_id}
