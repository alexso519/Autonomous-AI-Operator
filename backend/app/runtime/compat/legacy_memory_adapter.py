"""
Legacy memory adapter — bridges MemoryRuntime to kernel memory actions.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_manager import ContextManager
from app.runtime.action_system import ActionContext, ActionType, RuntimeAction


class LegacyMemoryAdapter:
    """Wrap memory operations for kernel dispatch."""

    @staticmethod
    def build_retrieval_action(
        execution_id: str,
        query: str,
        *,
        workflow_id: str = "",
        node_id: str = "",
    ) -> RuntimeAction:
        return RuntimeAction(
            action_type=ActionType.MEMORY_RETRIEVAL,
            context=ActionContext(
                execution_id=execution_id,
                workflow_id=workflow_id,
                node_id=node_id,
            ),
            payload={"query": query},
        )

    @staticmethod
    def sync_hierarchical(ctx: ContextManager) -> None:
        from app.execution.hierarchical_memory import HierarchicalMemory
        from app.execution.memory_runtime import MemoryRuntime

        memory = HierarchicalMemory(ctx)
        memory.sync_from_context()
        mem_rt = MemoryRuntime.get(ctx.execution_id)
        if mem_rt:
            mem_rt.coordinator.sync_hierarchical()

    @staticmethod
    def export_memory_state(ctx: ContextManager) -> dict[str, Any]:
        state = ctx.get_state()
        return {
            "workflowMemory": state.get("workflow_memory", {}),
            "outputs": ctx.get_all_outputs(),
        }
