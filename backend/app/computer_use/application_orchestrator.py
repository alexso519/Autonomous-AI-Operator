"""
Application orchestrator — coordinate actions across multiple applications.
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
class AppTransition:
    from_app: str
    to_app: str
    reason: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fromApp": self.from_app,
            "toApp": self.to_app,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


class ApplicationOrchestrator:
    """Coordinate multi-application desktop workflows."""

    _active_app: dict[str, str] = {}
    _transitions: dict[str, list[AppTransition]] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    async def switch_application(
        self,
        app_name: str,
        *,
        reason: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        from_app = self._active_app.get(self.execution_id, "")
        self._active_app[self.execution_id] = app_name

        transition = AppTransition(from_app=from_app, to_app=app_name, reason=reason)
        self._transitions.setdefault(self.execution_id, []).append(transition)

        result = {"success": True, "app": app_name}
        try:
            from app.computer_use.application_launcher import ApplicationLauncher
            launcher = ApplicationLauncher()
            launch = launcher.launch(app_name)
            result["launch"] = launch.to_dict()
        except Exception as exc:
            logger.warning("App switch launch failed: %s", exc)
            result["launchError"] = str(exc)

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "application_switched",
                "DesktopNavigator",
                f"Switched: {from_app or 'none'} → {app_name}",
                transition=transition.to_dict(),
            )
        return result

    async def run_cross_app_workflow(
        self,
        workflow_steps: list[dict[str, Any]],
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        workflow_id = f"xapp-{uuid.uuid4().hex[:8]}"
        if emit_fn:
            await emit_fn(
                self.execution_id,
                "cross_app_workflow_started",
                "WorkflowExecutor",
                f"Cross-app workflow ({len(workflow_steps)} steps)",
                workflowId=workflow_id,
                steps=workflow_steps,
            )

        results: list[dict[str, Any]] = []
        for step in workflow_steps:
            app = step.get("app", "")
            action = step.get("action", "observe")
            target = step.get("target", "")

            if app:
                await self.switch_application(app, reason=f"step: {action}", emit_fn=emit_fn)

            from app.computer_use.system_interaction_layer import SystemInteractionLayer
            layer = SystemInteractionLayer(self.execution_id)
            step_result = await layer.execute(action, target, step, emit_fn=emit_fn)
            results.append(step_result)

        return {
            "workflowId": workflow_id,
            "success": all(r.get("success", False) for r in results),
            "results": results,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "activeApp": self._active_app.get(self.execution_id, ""),
            "transitions": [t.to_dict() for t in self._transitions.get(self.execution_id, [])],
        }

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._active_app.pop(execution_id, None)
        cls._transitions.pop(execution_id, None)
