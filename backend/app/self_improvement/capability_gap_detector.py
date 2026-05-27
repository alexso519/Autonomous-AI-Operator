"""
Capability gap detector — missing tools, orchestration weaknesses, hallucination zones.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class CapabilityGapDetector:
    @classmethod
    async def detect(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        analysis: dict[str, Any],
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        tool_calls = ctx_snapshot.get("tool_calls") or []
        used_tools = {str(t.get("tool") or t.get("name", "")) for t in tool_calls if isinstance(t, dict)}

        if not used_tools and ctx_snapshot.get("status") != "completed":
            gap = cls._make_gap("tool_grounding", "No tools invoked during failed execution", 0.8)
            gaps.append(gap)

        for weakness in analysis.get("weaknesses") or []:
            wtype = weakness.get("type", "")
            if wtype == "tool_grounding_absent":
                gaps.append(cls._make_gap("hallucination_zone", "Potential hallucination zone — no tool grounding", 0.75))
            elif wtype == "coordination_inefficiency":
                gaps.append(cls._make_gap("coordination", "Multi-agent coordination inefficiency detected", 0.77))

        for bottleneck in analysis.get("bottlenecks") or []:
            btype = bottleneck.get("type", "")
            if btype == "retry_storm":
                gaps.append(cls._make_gap("retry_resilience", "Retry storm indicates missing retry strategy", 0.85))

        try:
            from app.runtime.capability_registry import CapabilityRegistry

            registry = CapabilityRegistry.for_execution(execution_id)
            registered = {c.id for c in registry.list_all()}
            expected = {"web_search", "calculator", "file_read"}
            missing = expected - registered - used_tools
            for cap in missing:
                gaps.append(cls._make_gap(f"missing_capability:{cap}", f"Capability not exercised: {cap}", 0.7))
        except Exception:
            pass

        persisted: list[dict[str, Any]] = []
        for gap in gaps:
            gap_id = f"gap-{uuid.uuid4().hex[:12]}"
            row = {
                "id": gap_id,
                "gap_key": gap["key"],
                "gap_data": json.dumps(gap),
                "severity": gap.get("severity", "info"),
                "confidence": gap["confidence"],
                "created_at": now,
            }
            await MetaReasoningMemory.insert_row("capability_gaps", row)
            persisted.append({**gap, "id": gap_id})
            if emit_fn and gap["confidence"] >= 0.75:
                await emit_fn(
                    execution_id,
                    "capability_gap_detected",
                    "self_improvement",
                    gap["description"],
                    gapId=gap_id,
                    gapKey=gap["key"],
                    confidence=gap["confidence"],
                )

        return persisted

    @classmethod
    def _make_gap(cls, key: str, description: str, confidence: float) -> dict[str, Any]:
        severity = "critical" if confidence >= 0.85 else "warning" if confidence >= 0.7 else "info"
        return {
            "key": key,
            "description": description,
            "confidence": confidence,
            "severity": severity,
            "recommendationOnly": True,
        }
