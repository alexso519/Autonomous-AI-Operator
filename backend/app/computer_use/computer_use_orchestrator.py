"""
Computer-use orchestrator — multimodal desktop pipeline with browser hybrid mode.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.computer_use.computer_use_agents import (
    is_computer_use_pipeline_objective,
    is_desktop_objective,
)
from app.computer_use.desktop_runtime import DesktopRuntime
from app.execution.context_manager import ContextManager
from app.config.settings import settings

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class ComputerUseOrchestrator:
    """Plan and execute multimodal desktop computer-use workflows."""

    PIPELINE_NAME = "computer_use_pipeline"

    @classmethod
    def is_computer_use_objective(cls, objective: str) -> bool:
        return is_computer_use_pipeline_objective(objective)

    @classmethod
    async def execute_computer_use_pipeline(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        """Run desktop workflow: perceive → ground → act → validate → synthesize."""
        if not settings.enable_computer_use:
            await emit_fn(
                execution_id, "log", "WorkflowExecutor",
                "Computer use disabled (ENABLE_COMPUTER_USE=0)",
            )
            return {"success": False, "error": "disabled"}

        mode = "hybrid" if "http" in objective.lower() or "browser" in objective.lower() else "desktop"
        ctx.set_workflow_memory("computer_use_plan", {"objective": objective, "mode": mode})

        await emit_fn(
            execution_id,
            "log",
            "WorkflowExecutor",
            f"Starting computer-use pipeline ({mode})",
            mode=mode,
            objective=objective[:200],
        )

        runtime = DesktopRuntime(execution_id)
        try:
            await runtime.start(emit_fn=emit_fn, mode=mode)
            result = await runtime.execute_objective(objective, emit_fn=emit_fn)

            visual_context = runtime.multimodal.get_visual_context_for_cognition()
            ctx.set_workflow_memory("visual_context", visual_context)
            ctx.set_workflow_memory("computer_use_result", result)
            ctx.set_workflow_memory("replay_history", runtime.get_replay_history())

            summary_parts = [
                "## Computer Use Summary\n",
                f"**Objective:** {objective[:200]}\n",
                f"**Mode:** {mode}\n",
                f"**Success:** {result.get('success', False)}\n",
                f"**Steps:** {len(result.get('results', []))}\n",
            ]
            if result.get("failures"):
                summary_parts.append(f"**Failures:** {result['failures']}\n")

            latest = visual_context.get("visualMemory", {}).get("latestSnapshot")
            if latest:
                summary_parts.append(f"**Last snapshot:** {latest.get('imagePath', 'n/a')}\n")
                elements = latest.get("elements", [])
                if elements:
                    summary_parts.append(f"**UI elements detected:** {len(elements)}\n")

            summary = "".join(summary_parts)
            ctx.add_workflow_output("computer_use_summary", summary)
            ctx.set_workflow_memory("computer_use_pipeline_completed", True)
            await ctx.persist()

            await emit_fn(
                execution_id,
                "output",
                "WorkflowExecutor",
                summary,
                pipeline=cls.PIPELINE_NAME,
                success=result.get("success", False),
            )

            return {
                "success": result.get("success", False),
                "summary": summary,
                "result": result,
                "visualContext": visual_context,
                "replayHistory": runtime.get_replay_history(),
            }
        finally:
            await runtime.close()

    @classmethod
    async def inject_visual_context_into_cognition(
        cls,
        execution_id: str,
        ctx: ContextManager,
    ) -> dict[str, Any]:
        """Return stored visual context for cognitive orchestrator."""
        stored = ctx.get_workflow_memory("visual_context")
        if stored:
            return stored
        if is_desktop_objective(ctx.get_workflow_memory("objective") or ""):
            from app.computer_use.multimodal_loop import MultimodalLoop
            loop = MultimodalLoop(execution_id)
            return loop.get_visual_context_for_cognition()
        return {}
