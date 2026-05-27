"""
Hierarchy evolution — agent specialization and topology-aware planning.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class HierarchyEvolution:
    @classmethod
    async def evolve(
        cls,
        ctx_snapshot: dict[str, Any],
        delegation_profile: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        rows = await MetaReasoningMemory.query_recent("hierarchy_generations", limit=1)
        generation = (int(rows[0]["generation"]) + 1) if rows else 1

        strategy = delegation_profile.get("strategy", "flat_delegation")
        hierarchy = {
            "generation": generation,
            "topology": cls._topology_for_strategy(strategy),
            "specialization": cls._specialization_hints(ctx_snapshot),
            "score": delegation_profile.get("effectiveness", 0.7),
            "recommendationOnly": True,
        }

        gen_id = f"hg-{uuid.uuid4().hex[:12]}"
        await MetaReasoningMemory.insert_row(
            "hierarchy_generations",
            {
                "id": gen_id,
                "generation": generation,
                "hierarchy_data": json.dumps(hierarchy),
                "score": hierarchy["score"],
                "created_at": now,
            },
        )
        return {**hierarchy, "id": gen_id}

    @classmethod
    def _topology_for_strategy(cls, strategy: str) -> dict[str, Any]:
        topologies = {
            "hierarchical_delegation": {"layers": 2, "coordinator": "orchestrator", "workers": "specialists"},
            "flat_delegation": {"layers": 1, "peers": True},
            "direct_assignment": {"layers": 1, "singleAgent": True},
            "single_owner_with_fallback": {"layers": 1, "owner": "primary", "fallback": "recovery_agent"},
        }
        return topologies.get(strategy, topologies["flat_delegation"])

    @classmethod
    def _specialization_hints(cls, ctx: dict[str, Any]) -> list[str]:
        hints: list[str] = []
        tool_calls = ctx.get("tool_calls") or []
        if tool_calls:
            hints.append("tool_specialist")
        if ctx.get("cognitive_deliberation"):
            hints.append("reasoning_specialist")
        if int(ctx.get("global_retry_count") or 0) > 2:
            hints.append("recovery_specialist")
        return hints or ["generalist"]
