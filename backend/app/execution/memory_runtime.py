"""
MemoryRuntime — unified memory + context orchestration facade.

Architecture:
  MemoryRuntime
  ├── ContextAssembler
  ├── MemoryCoordinator
  ├── RetrievalPlanner
  ├── MemoryRanker
  ├── ContextBudgetManager
  ├── MemoryLifecycleManager (existing)
  └── MemoryTelemetry
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.execution.context_assembler import AssemblyResult, ContextAssembler
from app.execution.context_manager import ContextManager
from app.execution.memory_coordinator import MemoryCoordinator
from app.execution.memory_lifecycle import MemoryLifecycleManager
from app.execution.memory_telemetry import MemoryTelemetry
from app.execution.retrieval_planner import AssemblyRequest

EmitFn = Callable[..., Awaitable[None]]


class MemoryRuntime:
    """
    Per-execution memory runtime. Single entry point for context assembly
    and memory coordination.
    """

    _instances: dict[str, MemoryRuntime] = {}

    def __init__(
        self,
        execution_id: str,
        ctx: ContextManager,
    ) -> None:
        self.execution_id = execution_id
        self.ctx = ctx
        self.telemetry = MemoryTelemetry.for_execution(execution_id)
        self.coordinator = MemoryCoordinator(ctx, execution_id, self.telemetry)
        self.assembler = ContextAssembler(self.coordinator, self.telemetry)
        MemoryRuntime._instances[execution_id] = self

    @classmethod
    def get(cls, execution_id: str) -> MemoryRuntime | None:
        return cls._instances.get(execution_id)

    @classmethod
    def for_execution(
        cls,
        execution_id: str,
        ctx: ContextManager,
    ) -> MemoryRuntime:
        existing = cls._instances.get(execution_id)
        if existing:
            existing.ctx = ctx
            existing.coordinator = MemoryCoordinator(ctx, execution_id, existing.telemetry)
            existing.assembler = ContextAssembler(existing.coordinator, existing.telemetry)
            return existing
        return cls(execution_id, ctx)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._instances.pop(execution_id, None)
        MemoryTelemetry.cleanup(execution_id)

    async def assemble_context(
        self,
        *,
        model_tier: str = "standard",
        is_synthesis: bool = False,
        is_retry: bool = False,
        agent_name: str = "",
        agent_role: str = "",
        tool_plan_block: str = "",
        emit_fn: EmitFn | None = None,
    ) -> AssemblyResult:
        """Primary API for engine context assembly."""
        return await self.assembler.assemble_simple(
            self.ctx,
            model_tier=model_tier,
            is_synthesis=is_synthesis,
            is_retry=is_retry,
            agent_name=agent_name,
            agent_role=agent_role,
            tool_plan_block=tool_plan_block,
            emit_fn=emit_fn,
        )

    async def assemble_with_request(
        self,
        request: AssemblyRequest,
        emit_fn: EmitFn | None = None,
    ) -> AssemblyResult:
        return await self.assembler.assemble(request, emit_fn=emit_fn)

    def get_debug_snapshot(self) -> dict[str, Any]:
        trace = self.coordinator.get_last_trace()
        return {
            "executionId": self.execution_id,
            "telemetry": self.telemetry.get_debug_snapshot(),
            "lastTrace": {
                "traceId": trace.trace_id,
                "strategy": trace.strategy,
                "retrieved": trace.records_retrieved,
                "afterRank": trace.records_after_rank,
            }
            if trace
            else None,
            "lastAssembly": self.ctx.get_workflow_memory("last_context_assembly"),
        }

    @classmethod
    async def run_lifecycle_cleanup(cls) -> dict[str, int]:
        """Delegate to existing lifecycle manager."""
        result = await MemoryLifecycleManager.rolling_cleanup()
        try:
            from app.intelligence.memory_safety import MemorySafety

            compaction = await MemorySafety.run_compaction_job()
            result.update(compaction)
        except Exception:
            pass
        return result

    async def retrieve_semantic(
        self,
        objective: str,
        *,
        limit: int = 5,
        emit_fn: EmitFn | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic long-term memory retrieval."""
        try:
            from app.intelligence.semantic_retriever import SemanticRetriever

            return await SemanticRetriever.retrieve_for_objective(
                objective,
                execution_id=self.execution_id,
                limit=limit,
                emit_fn=emit_fn,
            )
        except Exception:
            return []

    def get_intelligence_context(self) -> dict[str, Any]:
        return self.ctx.get_workflow_memory("intelligence_context") or {}
