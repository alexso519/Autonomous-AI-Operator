"""
Legacy context adapter — bridges ContextManager to kernel action context.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_manager import ContextManager
from app.runtime.action_system import ActionContext


class LegacyContextAdapter:
    """Convert between legacy ContextManager state and ActionContext."""

    @staticmethod
    def to_action_context(
        ctx: ContextManager,
        *,
        workflow_id: str = "",
        node_id: str = "",
        agent_name: str = "system",
        correlation_id: str = "",
    ) -> ActionContext:
        return ActionContext(
            execution_id=ctx.execution_id,
            workflow_id=workflow_id,
            node_id=node_id,
            agent_name=agent_name,
            correlation_id=correlation_id,
            metadata={"sharedMemoryKeys": list(ctx.get_state().get("workflow_memory", {}).keys())},
        )

    @staticmethod
    def sync_from_action(ctx: ContextManager, action_ctx: ActionContext) -> None:
        if action_ctx.metadata.get("output"):
            ctx.set_workflow_memory(f"action_{action_ctx.node_id}", action_ctx.metadata["output"])

    @staticmethod
    def export_state(ctx: ContextManager) -> dict[str, Any]:
        return ctx.get_state()

    @staticmethod
    async def persist(ctx: ContextManager) -> None:
        await ctx.persist()
