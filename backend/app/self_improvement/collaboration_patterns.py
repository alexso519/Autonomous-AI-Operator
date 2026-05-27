"""
Collaboration patterns — emergent coordination discovery and ranking.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)

_KNOWN_PATTERNS = [
    {"name": "parallel_fanout", "description": "Independent subtasks in parallel", "baseScore": 0.75},
    {"name": "sequential_pipeline", "description": "Strict dependency chain", "baseScore": 0.7},
    {"name": "consensus_deliberation", "description": "Multi-agent debate before action", "baseScore": 0.68},
    {"name": "specialist_routing", "description": "Route subtasks to domain specialists", "baseScore": 0.8},
    {"name": "leader_follower", "description": "Coordinator delegates to workers", "baseScore": 0.77},
]


class CollaborationPatterns:
    @classmethod
    async def discover_and_rank(
        cls,
        ctx_snapshot: dict[str, Any],
        analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        ranked: list[dict[str, Any]] = []

        parallel = int(ctx_snapshot.get("parallel_branch_count") or 0)
        coordination = analysis.get("coordinationScore", 0.7)

        for pattern in _KNOWN_PATTERNS:
            score = pattern["baseScore"]
            if pattern["name"] == "parallel_fanout" and parallel > 0:
                score += 0.08
            if pattern["name"] == "consensus_deliberation" and ctx_snapshot.get("cognitive_deliberation"):
                score += 0.05
            if coordination < 0.55 and pattern["name"] == "leader_follower":
                score += 0.1
            if analysis.get("retryStorm") and pattern["name"] == "sequential_pipeline":
                score -= 0.1
            score = min(1.0, max(0.3, score))

            pattern_id = f"cp-{uuid.uuid4().hex[:12]}"
            entry = {
                **pattern,
                "rankScore": score,
                "id": pattern_id,
                "recommendationOnly": True,
            }
            await MetaReasoningMemory.insert_row(
                "coordination_patterns",
                {
                    "id": pattern_id,
                    "pattern_key": pattern["name"],
                    "pattern_data": json.dumps(entry),
                    "rank_score": score,
                    "created_at": now,
                },
            )
            rank_id = f"cr-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "collaboration_rankings",
                {
                    "id": rank_id,
                    "pattern_name": pattern["name"],
                    "ranking_data": json.dumps(entry),
                    "rank_score": score,
                    "created_at": now,
                },
            )
            ranked.append(entry)

        ranked.sort(key=lambda x: x["rankScore"], reverse=True)
        return ranked
