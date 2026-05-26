"""
Adaptive model routing — deterministic, inspectable routing between local models.

Routes based on task complexity, context size, tool needs, synthesis requirements,
and latency sensitivity. Emits model_selected / model_fallback runtime events.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


class RoutingTier(str, Enum):
    LIGHTWEIGHT = "lightweight"
    STANDARD = "standard"
    REASONING = "reasoning"
    SYNTHESIS = "synthesis"


@dataclass
class RoutingContext:
    """Inputs used for deterministic routing."""

    task_complexity: str = "moderate"
    context_token_estimate: int = 0
    needs_tools: bool = False
    is_synthesis: bool = False
    is_final_output: bool = False
    latency_sensitive: bool = True
    agent_role: str = ""
    node_index: int = 0
    total_nodes: int = 1


@dataclass
class RoutingDecision:
    model: str
    tier: RoutingTier
    primary_model: str
    fallback_model: str
    reasons: list[str] = field(default_factory=list)
    used_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier.value,
            "primaryModel": self.primary_model,
            "fallbackModel": self.fallback_model,
            "reasons": self.reasons,
            "usedFallback": self.used_fallback,
        }


class ModelRouter:
    """Deterministic model selection with persisted routing history."""

    TIER_MODELS: dict[RoutingTier, str] = {
        RoutingTier.LIGHTWEIGHT: "qwen2.5:3b",
        RoutingTier.STANDARD: "qwen2.5:3b",
        RoutingTier.REASONING: "qwen2.5:7b",
        RoutingTier.SYNTHESIS: "qwen2.5:7b",
    }

    CONTEXT_LARGE_THRESHOLD = 4000
    CONTEXT_HUGE_THRESHOLD = 8000

    @classmethod
    def resolve_models(cls) -> dict[RoutingTier, str]:
        """Apply settings overrides per tier."""
        return {
            RoutingTier.LIGHTWEIGHT: settings.ollama_model_light or settings.ollama_model,
            RoutingTier.STANDARD: settings.ollama_model,
            RoutingTier.REASONING: settings.ollama_model_strong or settings.ollama_model,
            RoutingTier.SYNTHESIS: settings.ollama_model_strong or settings.ollama_model,
        }

    @classmethod
    def route(cls, ctx: RoutingContext) -> RoutingDecision:
        """Select model tier using deterministic rules."""
        tier_models = cls.resolve_models()
        reasons: list[str] = []
        fallback = tier_models[RoutingTier.LIGHTWEIGHT]

        if ctx.is_synthesis or ctx.is_final_output:
            tier = RoutingTier.SYNTHESIS
            reasons.append("synthesis_or_final_output")
        elif ctx.context_token_estimate >= cls.CONTEXT_HUGE_THRESHOLD:
            tier = RoutingTier.REASONING
            reasons.append("large_context_requires_stronger_model")
        elif ctx.task_complexity == "complex":
            tier = RoutingTier.REASONING
            reasons.append("complex_task")
        elif ctx.needs_tools and ctx.task_complexity != "simple":
            tier = RoutingTier.STANDARD
            reasons.append("tool_usage_non_trivial")
        elif ctx.task_complexity == "simple" and not ctx.needs_tools:
            tier = RoutingTier.LIGHTWEIGHT
            reasons.append("simple_task_no_tools")
        elif ctx.context_token_estimate >= cls.CONTEXT_LARGE_THRESHOLD:
            tier = RoutingTier.STANDARD
            reasons.append("moderate_context_size")
        else:
            tier = RoutingTier.STANDARD
            reasons.append("default_standard_tier")

        role_lower = ctx.agent_role.lower()
        if any(k in role_lower for k in ("synthes", "review", "planner", "analyst")):
            if tier == RoutingTier.LIGHTWEIGHT:
                tier = RoutingTier.STANDARD
                reasons.append("role_requires_standard_model")

        primary = tier_models[tier]
        return RoutingDecision(
            model=primary,
            tier=tier,
            primary_model=primary,
            fallback_model=fallback,
            reasons=reasons,
        )

    @classmethod
    async def select_model(
        cls,
        routing_ctx: RoutingContext,
        execution_id: str | None = None,
        emit_fn: Any = None,
        node_id: str | None = None,
        agent_name: str = "system",
    ) -> RoutingDecision:
        """Route, persist history, and optionally emit model_selected."""
        decision = cls.route(routing_ctx)
        if execution_id:
            await cls.persist_routing(
                execution_id=execution_id,
                node_id=node_id,
                agent_name=agent_name,
                decision=decision,
                routing_ctx=routing_ctx,
            )
        if execution_id and emit_fn:
            await emit_fn(
                execution_id,
                "model_selected",
                agent_name,
                f"Model routed: {decision.model} ({decision.tier.value})",
                nodeId=node_id,
                model=decision.model,
                tier=decision.tier.value,
                reasons=decision.reasons,
            )
        return decision

    @classmethod
    async def apply_fallback(
        cls,
        execution_id: str,
        original: RoutingDecision,
        error: str,
        emit_fn: Any = None,
        node_id: str | None = None,
        agent_name: str = "system",
    ) -> RoutingDecision:
        """Fall back to lightweight model after primary failure."""
        fallback_model = original.fallback_model
        decision = RoutingDecision(
            model=fallback_model,
            tier=RoutingTier.LIGHTWEIGHT,
            primary_model=original.primary_model,
            fallback_model=fallback_model,
            reasons=original.reasons + [f"fallback_after_error:{error[:80]}"],
            used_fallback=True,
        )
        await cls.persist_routing(
            execution_id=execution_id,
            node_id=node_id,
            agent_name=agent_name,
            decision=decision,
            routing_ctx=None,
            event_type="model_fallback",
        )
        if emit_fn:
            await emit_fn(
                execution_id,
                "model_fallback",
                agent_name,
                f"Falling back to {fallback_model}",
                nodeId=node_id,
                fromModel=original.primary_model,
                toModel=fallback_model,
                error=error[:200],
            )
        return decision

    @classmethod
    async def persist_routing(
        cls,
        execution_id: str,
        node_id: str | None,
        agent_name: str,
        decision: RoutingDecision,
        routing_ctx: RoutingContext | None,
        event_type: str = "model_selected",
    ) -> None:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO model_routing_history
                   (id, execution_id, node_id, agent_name, event_type, model,
                    tier, primary_model, fallback_model, reasons, context, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex[:16],
                    execution_id,
                    node_id or "",
                    agent_name,
                    event_type,
                    decision.model,
                    decision.tier.value,
                    decision.primary_model,
                    decision.fallback_model,
                    json.dumps(decision.reasons),
                    json.dumps(
                        routing_ctx.__dict__ if routing_ctx else {},
                        default=str,
                    ),
                    now,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Failed to persist routing history: %s", exc)

    @classmethod
    def estimate_context_tokens(cls, text: str) -> int:
        """Rough token estimate (chars / 4)."""
        return max(0, len(text) // 4)
