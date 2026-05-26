"""
Autonomous workflow executor — long-running multi-step desktop workflows.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowStepResult:
    step_index: int
    action: str
    target: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stepIndex": self.step_index,
            "action": self.action,
            "target": self.target,
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }


class WorkflowExecutor:
    """Execute long multi-step desktop workflows with checkpointing."""

    _active_chains: dict[str, str] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    async def execute_workflow(
        self,
        objective: str,
        steps: list[dict[str, Any]],
        *,
        chain_id: str | None = None,
        emit_fn: EmitFn | None = None,
        ctx: Any = None,
    ) -> dict[str, Any]:
        from app.computer_use.task_chain_manager import TaskChainManager
        from app.computer_use.action_scheduler import ActionScheduler
        from app.computer_use.recovery_executor import RecoveryExecutor
        from app.computer_use.multimodal_loop import MultimodalLoop

        cid = chain_id or f"chain-{uuid.uuid4().hex[:10]}"
        self._active_chains[self.execution_id] = cid
        chain_mgr = TaskChainManager(self.execution_id, cid)
        scheduler = ActionScheduler(self.execution_id)
        recovery = RecoveryExecutor(self.execution_id)
        loop = MultimodalLoop(self.execution_id)

        await chain_mgr.start(objective, steps, emit_fn=emit_fn)
        results: list[WorkflowStepResult] = []
        start_index = await chain_mgr.resume_index()

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "workflow_chain_started",
                "WorkflowExecutor",
                f"Workflow chain {cid[:8]} ({len(steps)} steps)",
                chainId=cid,
                stepCount=len(steps),
                resumeFrom=start_index,
            )

        for i in range(start_index, len(steps)):
            step = steps[i]
            action = step.get("action", "observe")
            target = step.get("target", "")

            scheduled = await scheduler.schedule(action, target, step_index=i)
            try:
                if action in ("click", "type", "scroll", "observe", "launch"):
                    from app.computer_use.action_planner import PlannedStep
                    planned = PlannedStep(action=action, target=target, confidence=0.7)
                    step_result = await loop.cycle.execute_step(planned, emit_fn=emit_fn)
                    success = step_result.success
                    output = step_result.to_dict()
                else:
                    success = True
                    output = {"action": action, "target": target, "skipped": True}

                if not success:
                    recovered = await recovery.attempt_recovery(
                        action, target, step_result.error if hasattr(step_result, "error") else "",
                        emit_fn=emit_fn,
                    )
                    if recovered.get("recovered"):
                        success = True
                        output["recovery"] = recovered

                wr = WorkflowStepResult(i, action, target, success, output=output)
                results.append(wr)
                await chain_mgr.complete_step(i, wr.to_dict())

                if emit_fn:
                    await emit_fn(
                        self.execution_id,
                        "workflow_step_completed",
                        "WorkflowExecutor",
                        f"Step {i + 1}/{len(steps)}: {action} → {target[:40]}",
                        chainId=cid,
                        step=wr.to_dict(),
                    )

                await chain_mgr.checkpoint(i, {"step": wr.to_dict()})

            except Exception as exc:
                logger.warning("Workflow step %d failed: %s", i, exc)
                wr = WorkflowStepResult(i, action, target, False, error=str(exc))
                results.append(wr)
                branch = await chain_mgr.create_branch(i, str(exc), emit_fn=emit_fn)
                if branch:
                    break

        success_count = sum(1 for r in results if r.success)
        summary = {
            "chainId": cid,
            "success": success_count == len(steps),
            "completedSteps": len(results),
            "totalSteps": len(steps),
            "results": [r.to_dict() for r in results],
        }

        if ctx:
            ctx.set_workflow_memory("computer_workflow_result", summary)
            await ctx.persist()

        self._active_chains.pop(self.execution_id, None)
        return summary
