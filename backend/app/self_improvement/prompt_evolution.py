"""
Prompt template evolution — role-specific tuning, token efficiency, no source rewrites.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.safety_limits import limits_from_settings

EmitFn = Callable[..., Awaitable[None]]


class PromptEvolution:
    @classmethod
    async def evolve_from_execution(
        cls,
        execution_id: str,
        status: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not settings.enable_prompt_evolution:
            return {"skipped": True, "reason": "disabled"}

        limits = limits_from_settings()
        existing = await ImprovementMemoryStore.query_recent("prompt_variants", limit=limits.max_prompt_variants)
        if len(existing) >= limits.max_prompt_variants:
            return {"skipped": True, "reason": "max_variants"}

        reflections = ctx_snapshot.get("reflection_history") or []
        quality_hint = "concise" if status == "completed" else "verify_facts"
        variant_id = f"pv-{uuid.uuid4().hex[:12]}"
        template = (
            f"[Optimized {quality_hint}] Focus on evidence-backed outputs. "
            f"Minimize repetition. Status context: {status}."
        )
        effectiveness = 0.72 if status == "completed" else 0.48

        if not dry_run:
            now = datetime.now(timezone.utc).isoformat()
            await ImprovementMemoryStore.insert_row(
                "prompt_variants",
                {
                    "id": variant_id,
                    "role": "agent",
                    "template_key": f"exec_{execution_id[:8]}",
                    "template_body": template[:2000],
                    "effectiveness": effectiveness,
                    "token_efficiency": 0.65 if status == "completed" else 0.5,
                    "metadata": json.dumps({"dryRun": False}),
                    "active": 0,
                    "created_at": now,
                },
            )

        result = {
            "variantId": variant_id,
            "role": "agent",
            "effectiveness": effectiveness,
            "dryRun": dry_run,
            "templatePreview": template[:120],
        }

        if emit_fn:
            await emit_fn(
                execution_id,
                "prompt_optimized",
                "self_improvement",
                "Prompt variant evolved (bounded)",
                **result,
            )
        return result
