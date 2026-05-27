"""
Tool gap analyzer — identify missing tools and recommend configs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory

logger = logging.getLogger(__name__)


class ToolGapAnalyzer:
    @classmethod
    async def analyze(
        cls,
        ctx_snapshot: dict[str, Any],
        gaps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        gap_keys = {g.get("key", "") for g in gaps}
        recommendations: list[dict[str, Any]] = []

        if any("missing_capability" in k for k in gap_keys):
            recommendations.append({
                "toolName": "web_search",
                "reason": "Search grounding recommended for objective class",
                "config": {"timeout": 30, "maxResults": 5},
                "confidence": 0.76,
            })

        if any("hallucination" in k for k in gap_keys):
            recommendations.append({
                "toolName": "calculator",
                "reason": "Deterministic computation reduces hallucination risk",
                "config": {"precision": "high"},
                "confidence": 0.8,
            })

        tool_calls = ctx_snapshot.get("tool_calls") or []
        if not tool_calls:
            recommendations.append({
                "toolName": "file_read",
                "reason": "No tools invoked — suggest file_read for context grounding",
                "config": {"maxBytes": 65536},
                "confidence": 0.74,
            })

        for rec in recommendations:
            report_id = f"tg-{uuid.uuid4().hex[:12]}"
            await MetaReasoningMemory.insert_row(
                "tool_gap_reports",
                {
                    "id": report_id,
                    "tool_name": rec["toolName"],
                    "gap_data": json.dumps(rec),
                    "confidence": rec["confidence"],
                    "created_at": now,
                },
            )
            reports.append({**rec, "id": report_id, "recommendationOnly": True})
        return reports
