"""
Tool selection optimization — learns preferred/avoided tools from execution history.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore

EmitFn = Callable[..., Awaitable[None]]


class ToolSelectionOptimizer:
    @classmethod
    async def optimize(
        cls,
        execution_id: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        tool_calls = ctx_snapshot.get("tool_calls") or []
        names = [tc.get("tool") or tc.get("toolName") or "" for tc in tool_calls if tc]
        counts = Counter(n for n in names if n)
        preferred = [t for t, _ in counts.most_common(5)]
        failed = [
            tc.get("tool") or tc.get("toolName")
            for tc in tool_calls
            if tc.get("status") == "failed" or tc.get("error")
        ]
        avoided = list({f for f in failed if f})[:5]

        profile = {"preferredTools": preferred, "avoidedTools": avoided}

        if not dry_run and (preferred or avoided):
            pid = f"tsp-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            await ImprovementMemoryStore.insert_row(
                "tool_selection_profiles",
                {
                    "id": pid,
                    "profile_key": f"tools_{execution_id[:8]}",
                    "preferred_tools": json.dumps(preferred),
                    "avoided_tools": json.dumps(avoided),
                    "profile_data": json.dumps(profile),
                    "effectiveness": 0.65 if preferred else 0.5,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        if emit_fn and (preferred or avoided):
            await emit_fn(
                execution_id,
                "tool_selection_optimized",
                "self_improvement",
                f"Tool profile: {len(preferred)} preferred, {len(avoided)} avoided",
                profile=profile,
                dryRun=dry_run,
            )
        return {"profile": profile, "dryRun": dry_run}
