"""
Autonomous capability discovery — missing runtime capabilities and orchestration improvements.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.agent_architect import AgentArchitect
from app.self_improvement.autonomy_boundaries import AutonomyBoundaries
from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.tool_gap_analyzer import ToolGapAnalyzer
from app.self_improvement.workflow_evolution import WorkflowEvolution

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class CapabilityDiscovery:
    @classmethod
    async def discover(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        meta_reflection: dict[str, Any],
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not EvolutionGovernor.is_capability_discovery_enabled():
            return {"skipped": True, "reason": "capability_discovery_disabled"}

        analysis = meta_reflection.get("analysis") or {}
        gaps = meta_reflection.get("gaps") or []

        capabilities = await cls._discover_capabilities(execution_id, gaps, analysis, emit_fn)
        archetypes = await AgentArchitect.propose_archetypes(ctx_snapshot, gaps, analysis)
        tool_gaps = await ToolGapAnalyzer.analyze(ctx_snapshot, gaps)
        workflow_evolutions = await WorkflowEvolution.propose_evolutions(ctx_snapshot, analysis)

        for cap in capabilities:
            if cap.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "new_capability_discovered",
                    "self_improvement",
                    cap.get("description", "New capability discovered"),
                    capabilityId=cap.get("id"),
                    capabilityKey=cap.get("key"),
                )

        for arch in archetypes:
            if arch.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "agent_archetype_proposed",
                    "self_improvement",
                    f"Agent archetype proposed: {arch.get('archetypeName')}",
                    proposalId=arch.get("id"),
                    archetypeName=arch.get("archetypeName"),
                )

        for tg in tool_gaps:
            if tg.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "tool_gap_identified",
                    "self_improvement",
                    f"Tool gap: {tg.get('toolName')}",
                    reportId=tg.get("id"),
                    toolName=tg.get("toolName"),
                )

        for we in workflow_evolutions:
            if we.get("confidence", 0) >= 0.75:
                await emit_fn(
                    execution_id,
                    "workflow_evolution_proposed",
                    "self_improvement",
                    we.get("suggestion", "Workflow evolution proposed"),
                    evolutionId=we.get("id"),
                    evolutionType=we.get("evolutionType"),
                )

        return {
            "capabilities": capabilities,
            "archetypes": archetypes,
            "toolGaps": tool_gaps,
            "workflowEvolutions": workflow_evolutions,
        }

    @classmethod
    async def _discover_capabilities(
        cls,
        execution_id: str,
        gaps: list[dict[str, Any]],
        analysis: dict[str, Any],
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        discovered: list[dict[str, Any]] = []

        candidates = [
            ("semantic_memory_lookup", "Long-term semantic memory for cross-execution recall", 0.72),
            ("adaptive_retry_policy", "Context-aware retry with failure classification", 0.8),
            ("parallel_subtask_fanout", "Parallel subtask execution for independent branches", 0.74),
        ]

        if analysis.get("retryStorm"):
            candidates.append(("circuit_breaker", "Circuit breaker for retry storm prevention", 0.86))
        if any("hallucination" in g.get("key", "") for g in gaps):
            candidates.append(("grounding_verifier", "Post-action grounding verification", 0.83))

        for key, desc, confidence in candidates:
            cap = AutonomyBoundaries.sanitize_proposal({
                "type": "discovered_capability",
                "key": key,
                "description": desc,
                "confidence": confidence,
                "status": "proposed",
            })
            cap_id = f"dc-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "discovered_capabilities",
                {
                    "id": cap_id,
                    "capability_key": key,
                    "capability_data": json.dumps(cap),
                    "confidence": confidence,
                    "status": "proposed",
                    "created_at": now,
                },
            )
            discovered.append({**cap, "id": cap_id})

        return discovered

    @classmethod
    async def get_explorer_data(cls, limit: int = 30) -> dict[str, Any]:
        return {
            "capabilities": await MetaReasoningMemory.query_recent("discovered_capabilities", limit=limit),
            "archetypes": await MetaReasoningMemory.query_recent("agent_archetype_proposals", limit=limit),
            "toolGaps": await MetaReasoningMemory.query_recent("tool_gap_reports", limit=limit),
            "workflowEvolutions": await MetaReasoningMemory.query_recent("workflow_evolution_history", limit=limit),
        }
