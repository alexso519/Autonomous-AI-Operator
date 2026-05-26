"""
Perception-action cycle — single observe/reason/act iteration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.computer_use.action_planner import PlannedStep
from app.computer_use.desktop_state_tracker import DesktopStateTracker
from app.computer_use.interaction_reflection import InteractionReflection
from app.computer_use.mouse_keyboard_controller import MouseKeyboardController
from app.computer_use.screen_perception import ScreenPerception
from app.computer_use.ui_navigation_graph import UINavigationGraph
from app.computer_use.visual_grounding import VisualGrounding

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class CycleResult:
    step: PlannedStep
    success: bool
    perception: dict[str, Any] | None = None
    action_result: dict[str, Any] | None = None
    error: str = ""
    recovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.to_dict(),
            "success": self.success,
            "perception": self.perception,
            "actionResult": self.action_result,
            "error": self.error,
            "recovered": self.recovered,
        }


class PerceptionActionCycle:
    """Single iteration of observe → reason → act → validate."""

    def __init__(
        self,
        execution_id: str,
        perception: ScreenPerception,
        controller: MouseKeyboardController,
        grounding: VisualGrounding,
        nav_graph: UINavigationGraph,
        state_tracker: DesktopStateTracker,
        reflection: InteractionReflection,
    ) -> None:
        self.execution_id = execution_id
        self.perception = perception
        self.controller = controller
        self.grounding = grounding
        self.nav_graph = nav_graph
        self.state_tracker = state_tracker
        self.reflection = reflection

    async def execute_step(
        self,
        step: PlannedStep,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "WorkflowExecutor",
        attempt: int = 1,
    ) -> CycleResult:
        action = step.action

        if action in ("observe", "analyze", "validate"):
            result = await self.perception.perceive(emit_fn=emit_fn, agent=agent)
            self.state_tracker.update(snapshot_id=result.snapshot_id)
            return CycleResult(step=step, success=True, perception=result.to_dict())

        if action == "click":
            return await self._execute_click(step, emit_fn=emit_fn, agent=agent, attempt=attempt)

        if action == "type":
            text = step.value or step.target
            action_result = await self.controller.type_text(text, emit_fn=emit_fn, agent=agent)
            return CycleResult(
                step=step,
                success=action_result.success,
                action_result=action_result.to_dict(),
                error=action_result.error or "",
            )

        if action == "scroll":
            direction = step.target or "down"
            action_result = await self.controller.scroll(direction, emit_fn=emit_fn, agent=agent)
            return CycleResult(step=step, success=action_result.success, action_result=action_result.to_dict())

        if action in ("navigate", "launch"):
            return CycleResult(step=step, success=True, action_result={"delegated": action, "target": step.target})

        return CycleResult(step=step, success=False, error=f"unknown_action_{action}")

    async def _execute_click(
        self,
        step: PlannedStep,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "UIOperator",
        attempt: int = 1,
    ) -> CycleResult:
        if emit_fn:
            await emit_fn(
                self.execution_id, "ui_navigation_started", agent,
                f"Navigating to click '{step.target}'",
                target=step.target,
                attempt=attempt,
            )

        if not self.perception.memory.latest:
            await self.perception.perceive(emit_fn=emit_fn, agent=agent)

        target = await self.grounding.ground_and_emit(
            self.execution_id, step.target, emit_fn=emit_fn, agent=agent,
        )
        if target is None:
            error = f"grounding_failed:{step.target}"
            reflection = self.reflection.reflect("click", step.target, error, attempt=attempt)
            if reflection.should_retry and attempt < 3:
                await self.perception.perceive(emit_fn=emit_fn, agent=agent)
                return await self._execute_click(step, emit_fn=emit_fn, agent=agent, attempt=attempt + 1)
            return CycleResult(step=step, success=False, error=error)

        cx, cy = target.center
        prev_hash = self.perception.memory.state_hash()
        action_result = await self.controller.click(cx, cy, label=step.target, emit_fn=emit_fn, agent=agent)

        if not action_result.success:
            reflection = self.reflection.reflect("click", step.target, action_result.error or "", attempt=attempt)
            if reflection.should_retry and attempt < 3:
                if emit_fn:
                    await emit_fn(
                        self.execution_id, "interaction_recovered", agent,
                        f"Recovery: {reflection.recovery_strategy}",
                        strategy=reflection.recovery_strategy,
                    )
                await self.perception.perceive(emit_fn=emit_fn, agent=agent)
                return await self._execute_click(step, emit_fn=emit_fn, agent=agent, attempt=attempt + 1)
            return CycleResult(step=step, success=False, action_result=action_result.to_dict(), error=action_result.error or "")

        post = await self.perception.perceive(emit_fn=emit_fn, agent=agent)
        curr_hash = self.perception.memory.state_hash()
        self.nav_graph.record_transition(prev_hash, curr_hash, f"click:{step.target}")

        return CycleResult(
            step=step,
            success=True,
            perception=post.to_dict(),
            action_result=action_result.to_dict(),
            recovered=attempt > 1,
        )
