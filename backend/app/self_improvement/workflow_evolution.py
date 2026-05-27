"""
Workflow evolution — topology suggestions, planning strategy improvements.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class WorkflowEvolution:
    @classmethod
    async def propose_evolutions(
        cls,
        ctx_snapshot: dict[str, Any],
        analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        if analysis.get("retryStorm"):
            proposals.append({
                "evolutionType": "retry_topology",
                "suggestion": "Insert failure-classifier node before retry branch",
                "topologyDelta": {"addNode": "failure_classifier", "before": "retry"},
                "confidence": 0.81,
            })

        if analysis.get("coordinationScore", 1.0) < 0.6:
            proposals.append({
                "evolutionType": "parallelism",
                "suggestion": "Split independent subtasks into parallel branches",
                "topologyDelta": {"parallelBranches": 2},
                "confidence": 0.77,
            })

        reflections = ctx_snapshot.get("reflection_history") or []
        if len(reflections) > 6:
            proposals.append({
                "evolutionType": "planning_strategy",
                "suggestion": "Reduce reflection depth; use single-pass planning with tool grounding",
                "topologyDelta": {"maxReflections": 3},
                "confidence": 0.79,
            })

        try:
            from app.execution.adaptive_graph import AdaptiveExecutionGraph

            graph_hint = {"adaptiveGraphAvailable": True}
            if graph_hint:
                proposals.append({
                    "evolutionType": "execution_graph",
                    "suggestion": "Enable adaptive graph re-routing on bottleneck detection",
                    "topologyDelta": {"adaptiveReroute": True},
                    "confidence": 0.75,
                })
        except Exception:
            pass

        persisted: list[dict[str, Any]] = []
        for prop in proposals:
            prop_id = f"we-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "workflow_evolution_history",
                {
                    "id": prop_id,
                    "evolution_type": prop["evolutionType"],
                    "evolution_data": json.dumps(prop),
                    "confidence": prop["confidence"],
                    "created_at": now,
                },
            )
            persisted.append({**prop, "id": prop_id, "recommendationOnly": True})
        return persisted
