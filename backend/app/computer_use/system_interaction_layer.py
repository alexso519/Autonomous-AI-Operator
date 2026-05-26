"""
System interaction layer — detect and execute system-level interactions.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class SystemInteractionLayer:
    """Bridge system events and desktop actions."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    async def execute(
        self,
        action: str,
        target: str,
        step_data: dict[str, Any] | None = None,
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        interaction_type = self._classify_interaction(action, target, step_data or {})
        result: dict[str, Any] = {"action": action, "target": target, "success": False}

        try:
            if action == "launch":
                from app.computer_use.application_launcher import ApplicationLauncher
                launch = ApplicationLauncher().launch(target)
                result["launch"] = launch.to_dict()
                result["success"] = launch.success

            elif action in ("click", "type", "scroll", "observe"):
                from app.computer_use.action_planner import PlannedStep
                from app.computer_use.multimodal_loop import MultimodalLoop
                loop = MultimodalLoop(self.execution_id)
                planned = PlannedStep(action=action, target=target)
                step_result = await loop.cycle.execute_step(planned, emit_fn=emit_fn)
                result["step"] = step_result.to_dict()
                result["success"] = step_result.success

            elif action == "file":
                from app.computer_use.file_workflow_engine import FileWorkflowEngine
                engine = FileWorkflowEngine(self.execution_id)
                fw = await engine.run_file_workflow(
                    step_data.get("workflowType", "write_note") if step_data else "write_note",
                    source_path=step_data.get("sourcePath", "") if step_data else "",
                    dest_path=step_data.get("destPath", "") if step_data else "",
                    content=step_data.get("content", "") if step_data else "",
                    emit_fn=emit_fn,
                )
                result["fileWorkflow"] = fw
                result["success"] = fw.get("success", False)

            else:
                result["skipped"] = True
                result["success"] = True

        except Exception as exc:
            logger.warning("System interaction failed: %s", exc)
            result["error"] = str(exc)

        if emit_fn and interaction_type:
            await emit_fn(
                self.execution_id,
                "system_interaction_detected",
                "UIOperator",
                f"System interaction: {interaction_type}",
                interactionType=interaction_type,
                result=result,
            )
        return result

    def _classify_interaction(
        self,
        action: str,
        target: str,
        step_data: dict[str, Any],
    ) -> str:
        if action == "launch":
            return "app_launch"
        if action == "file":
            return step_data.get("workflowType", "file_operation")
        if "download" in target.lower():
            return "download"
        if "terminal" in target.lower() or action == "shell":
            return "terminal"
        if action in ("click", "type"):
            return "ui_interaction"
        return action
