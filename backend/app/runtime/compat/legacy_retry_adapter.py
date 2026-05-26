"""
Legacy retry adapter — bridges RetryCoordinator to kernel retry actions.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.contracts import RetryIntent, RetryRequest
from app.runtime.action_system import ActionContext, ActionPriority, ActionType, RuntimeAction


class LegacyRetryAdapter:
    """Wrap RetryCoordinator decisions as kernel RuntimeActions."""

    @staticmethod
    def build_retry_action(
        execution_id: str,
        node_id: str,
        intent: RetryIntent,
        reason: str = "",
        *,
        quality_score: Any = None,
        workflow_id: str = "",
    ) -> RuntimeAction:
        return RuntimeAction(
            action_type=ActionType.RETRY,
            context=ActionContext(
                execution_id=execution_id,
                workflow_id=workflow_id,
                node_id=node_id,
            ),
            payload={
                "intent": intent.value if hasattr(intent, "value") else str(intent),
                "reason": reason,
                "quality_score": quality_score,
            },
            priority=ActionPriority.HIGH,
        )

    @staticmethod
    def build_request_from_action(action: RuntimeAction) -> RetryRequest:
        return RetryRequest(
            node_id=action.context.node_id,
            intent=RetryIntent(action.payload.get("intent", "recovery")),
            reason=action.payload.get("reason", ""),
            quality_score=action.payload.get("quality_score"),
        )

    @staticmethod
    def evaluate_legacy(ctx: ContextManager, request: RetryRequest) -> Any:
        from app.execution.execution_runtime import ExecutionRuntime

        rt = ExecutionRuntime.get(ctx.execution_id)
        if rt is None:
            return None
        return rt.retry.evaluate(request)
