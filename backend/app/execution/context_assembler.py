"""
Central context assembler — single pipeline for all context creation.

Agents should not manually concatenate context blocks; all assembly
flows through ContextAssembler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.execution.context_budget_manager import (
    ContextBudgetManager,
    estimate_tokens,
)
from app.execution.context_manager import ContextManager
from app.execution.memory_coordinator import MemoryCoordinator
from app.execution.memory_ranker import RankedMemory
from app.execution.memory_telemetry import ContextAssemblyReport, MemoryTelemetry
from app.execution.retrieval_planner import AssemblyRequest, RetrievalPlanner

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class AssemblyResult:
    """Output of context assembly."""

    context: str
    metadata: dict[str, Any]
    sections: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    truncated_sections: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    replay_token: str = ""


# Ordered section headers for deterministic assembly
SECTION_ORDER = [
    "workflow_core",
    "initial_context",
    "tool_results",
    "evidence",
    "research",
    "shared_memory",
    "memory_tiers",
    "reasoning",
    "cognitive",
    "agent_outputs",
    "tool_plan",
]


class ContextAssembler:
    """
    Single context assembly pipeline with ordered sections,
    conflict resolution, and source attribution.
    """

    def __init__(
        self,
        coordinator: MemoryCoordinator,
        telemetry: MemoryTelemetry | None = None,
    ) -> None:
        self._coordinator = coordinator
        self.telemetry = telemetry or coordinator.telemetry

    @classmethod
    def for_context(
        cls,
        ctx: ContextManager,
        execution_id: str = "",
    ) -> ContextAssembler:
        coord = MemoryCoordinator(ctx, execution_id=execution_id)
        return cls(coord)

    async def assemble(
        self,
        request: AssemblyRequest,
        *,
        emit_fn: EmitFn | None = None,
    ) -> AssemblyResult:
        """
        Full assembly pipeline:
          plan → retrieve → rank → budget → merge → telemetry
        """
        ctx = self._coordinator.context_manager
        plan = RetrievalPlanner.plan(request)

        objective = ctx.get_initial_context()
        ranked = await self._coordinator.retrieve(
            plan,
            objective=objective,
            emit_fn=emit_fn,
        )

        profile = ContextBudgetManager.resolve_profile(
            request.model_tier,
            is_synthesis=request.is_synthesis,
            is_retry=request.is_retry,
        )
        allocation = ContextBudgetManager.allocate_sections(profile, ranked)

        # Core workflow block (outputs + tools + initial context)
        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        workflow_block, budget_meta = ContextBudgetManager.build_workflow_context(
            initial_context=ctx.get_initial_context(),
            outputs=ctx.get_state().get("outputs") or {},
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            profile=profile,
            is_synthesis=request.is_synthesis,
        )

        sections = self._build_sections(
            workflow_block=workflow_block,
            ranked=ranked,
            request=request,
            initial_context=ctx.get_initial_context(),
        )

        ordered = self._order_sections(sections)
        combined, truncated, chars_removed = ContextBudgetManager.assemble_sections(
            ordered,
            profile.max_total_chars,
        )

        sources = list({rm.record.source for rm in ranked})
        section_meta = [
            {"name": name, "chars": len(content), "preserved": preserve}
            for name, content, preserve in ordered
            if content.strip()
        ]

        estimated = estimate_tokens(combined)
        utilization = ContextBudgetManager.utilization(len(combined), profile.max_total_chars)

        metadata = {
            **budget_meta,
            "modelTier": request.model_tier,
            "isSynthesis": request.is_synthesis,
            "isRetry": request.is_retry,
            "estimatedTokens": estimated,
            "strategy": plan.strategy.value,
            "replayToken": plan.replay_token,
            "sources": sources,
            "truncatedSections": truncated,
        }

        report = ContextAssemblyReport(
            total_chars=len(combined),
            estimated_tokens=estimated,
            sections=section_meta,
            truncated_sections=truncated,
            budget_utilization=utilization,
            memory_pressure=utilization,
            sources=sources,
        )

        await self.telemetry.context_assembled(report, emit_fn)
        if truncated:
            await self.telemetry.context_truncated(truncated, chars_removed, emit_fn)
        if utilization > 0.85:
            await self.telemetry.memory_pressure_warning(utilization, 0.85, emit_fn)

        ctx.set_workflow_memory("last_context_budget", metadata)
        ctx.set_workflow_memory("last_context_assembly", {
            "replayToken": plan.replay_token,
            "sections": section_meta,
            "sources": sources,
        })

        return AssemblyResult(
            context=combined,
            metadata=metadata,
            sections=section_meta,
            sources=sources,
            truncated_sections=truncated,
            estimated_tokens=estimated,
            replay_token=plan.replay_token,
        )

    def _build_sections(
        self,
        workflow_block: str,
        ranked: list[RankedMemory],
        request: AssemblyRequest,
        initial_context: str,
    ) -> list[tuple[str, str, bool]]:
        """Build named sections from ranked memories and workflow block."""
        section_map: dict[str, list[str]] = {}

        # Core workflow block already contains initial context, tools, and outputs
        if workflow_block.strip():
            section_map["workflow_core"] = [workflow_block]

        for rm in ranked:
            section = rm.record.section or rm.record.namespace
            # Skip namespaces already covered by workflow_core compression
            if section in ("agent_outputs", "tool_results") and workflow_block.strip():
                if section == "tool_results" and "Tool Results" in workflow_block:
                    continue
                if section == "agent_outputs":
                    continue
            section_map.setdefault(section, []).append(rm.record.content)

        if request.tool_plan_block and request.include_tool_plan:
            section_map.setdefault("tool_plan", []).append(request.tool_plan_block)

        preserve_sections = {
            "workflow_core",
            "initial_context",
            "tool_results",
            "evidence",
            "tool_plan",
        }

        result: list[tuple[str, str, bool]] = []
        for name, lines in section_map.items():
            content = "\n".join(lines)
            if name == "research" and content.strip():
                content = f"--- Autonomous Research Evidence ---\n{content}"
            elif name == "memory_tiers" and content.strip():
                content = f"--- Memory ---\n{content}"
            result.append((name, content, name in preserve_sections))
        return result

    def _order_sections(
        self,
        sections: list[tuple[str, str, bool]],
    ) -> list[tuple[str, str, bool]]:
        """Deterministic section ordering."""
        order_index = {name: i for i, name in enumerate(SECTION_ORDER)}
        return sorted(
            sections,
            key=lambda s: (order_index.get(s[0], 99), s[0]),
        )

    def assemble_sync(
        self,
        request: AssemblyRequest,
    ) -> AssemblyResult:
        """Synchronous assembly (backward-compatible, no SSE telemetry)."""
        ctx = self._coordinator.context_manager
        plan = RetrievalPlanner.plan(request)
        objective = ctx.get_initial_context()
        ranked = self._coordinator.retrieve_sync(plan, objective=objective)

        profile = ContextBudgetManager.resolve_profile(
            request.model_tier,
            is_synthesis=request.is_synthesis,
            is_retry=request.is_retry,
        )

        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        workflow_block, budget_meta = ContextBudgetManager.build_workflow_context(
            initial_context=ctx.get_initial_context(),
            outputs=ctx.get_state().get("outputs") or {},
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            profile=profile,
            is_synthesis=request.is_synthesis,
        )

        sections = self._build_sections(
            workflow_block=workflow_block,
            ranked=ranked,
            request=request,
            initial_context=ctx.get_initial_context(),
        )
        ordered = self._order_sections(sections)
        combined, truncated, _chars_removed = ContextBudgetManager.assemble_sections(
            ordered,
            profile.max_total_chars,
        )

        sources = list({rm.record.source for rm in ranked})
        section_meta = [
            {"name": name, "chars": len(content), "preserved": preserve}
            for name, content, preserve in ordered
            if content.strip()
        ]
        estimated = estimate_tokens(combined)
        metadata = {
            **budget_meta,
            "modelTier": request.model_tier,
            "isSynthesis": request.is_synthesis,
            "isRetry": request.is_retry,
            "estimatedTokens": estimated,
            "strategy": plan.strategy.value,
            "replayToken": plan.replay_token,
            "sources": sources,
            "truncatedSections": truncated,
        }
        ctx.set_workflow_memory("last_context_budget", metadata)
        return AssemblyResult(
            context=combined,
            metadata=metadata,
            sections=section_meta,
            sources=sources,
            truncated_sections=truncated,
            estimated_tokens=estimated,
            replay_token=plan.replay_token,
        )

    async def assemble_simple(
        self,
        ctx: ContextManager,
        *,
        model_tier: str = "standard",
        is_synthesis: bool = False,
        is_retry: bool = False,
        agent_name: str = "",
        agent_role: str = "",
        tool_plan_block: str = "",
        emit_fn: EmitFn | None = None,
    ) -> AssemblyResult:
        """Convenience wrapper matching ContextManager.get_combined_context signature."""
        research_mode = bool(ctx.get_workflow_memory("autonomous_research_mode"))
        confidence = ctx.get_workflow_memory("last_confidence")
        if isinstance(confidence, dict):
            confidence = confidence.get("score")

        request = AssemblyRequest(
            model_tier=model_tier,
            is_synthesis=is_synthesis,
            is_retry=is_retry,
            agent_name=agent_name,
            agent_role=agent_role,
            task_complexity=str(ctx.get_workflow_memory("task_complexity") or "moderate"),
            confidence_score=float(confidence) if confidence is not None else None,
            research_mode=research_mode,
            tool_plan_block=tool_plan_block,
        )
        return await self.assemble(request, emit_fn=emit_fn)
