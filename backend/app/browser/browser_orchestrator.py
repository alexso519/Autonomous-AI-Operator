"""
Browser orchestrator — execute multi-step browser workflows with evidence synthesis.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.browser.action_planner import ActionPlanner
from app.browser.browser_runtime import BrowserRuntime
from app.execution.context_manager import ContextManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class BrowserOrchestrator:
    """Plan and execute browser automation workflows."""

    PIPELINE_NAME = "browser_automation_pipeline"

    @classmethod
    def is_browser_objective(cls, objective: str) -> bool:
        from app.computer_use.computer_use_agents import is_desktop_objective
        if is_desktop_objective(objective):
            return True
        lower = objective.lower()
        keywords = (
            "browser", "website", "web page", "navigate", "click",
            "github", "google", "open ", "login", "form", "screenshot",
        )
        research_browser = (
            "research" in lower and any(k in lower for k in ("news", "web", "online", "latest"))
        )
        return any(k in lower for k in keywords) or research_browser

    @classmethod
    async def execute_browser_pipeline(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        """Run browser workflow: plan → navigate → interact → capture → synthesize."""
        plan = ActionPlanner.plan(objective)
        ctx.set_workflow_memory("browser_plan", plan.to_dict())

        await emit_fn(
            execution_id,
            "log",
            "BrowserOperator",
            f"Starting browser pipeline: {plan.start_url}",
            plan=plan.to_dict(),
        )

        runtime = BrowserRuntime(execution_id)
        results: list[dict[str, Any]] = []

        try:
            await runtime.start()

            for step in plan.steps:
                if step.action == "navigate":
                    result = await runtime.navigate(step.target, emit_fn=emit_fn)
                    results.append(result.to_dict())
                elif step.action == "click":
                    result = await runtime.click(step.target, emit_fn=emit_fn)
                    results.append(result.to_dict())
                elif step.action == "fill":
                    parts = step.value.split("=", 1)
                    selector = parts[0] if parts else step.target
                    value = parts[1] if len(parts) > 1 else step.value
                    result = await runtime.fill_form(selector, value, emit_fn=emit_fn)
                    results.append(result.to_dict())
                elif step.action == "screenshot":
                    if runtime.memory.current_url:
                        result = await runtime.navigate(
                            runtime.memory.current_url, emit_fn=emit_fn
                        )
                        results.append(result.to_dict())
                elif step.action == "extract":
                    if runtime.memory.snapshots:
                        snap = runtime.memory.snapshots[-1]
                        results.append({
                            "actionType": "extract",
                            "url": snap.url,
                            "title": snap.title,
                            "contentExcerpt": snap.content_excerpt,
                        })

        finally:
            await runtime.close()

        # Build evidence-backed summary
        summary_parts = [
            f"## Browser Research Summary\n",
            f"**Objective:** {objective[:200]}\n",
            f"**Pages visited:** {len(runtime.memory.snapshots)}\n",
        ]

        for i, snap in enumerate(runtime.memory.snapshots[:5], 1):
            summary_parts.append(
                f"\n### Source {i}: {snap.title or snap.url}\n"
                f"- URL: {snap.url}\n"
                f"- Screenshot: {snap.screenshot_path or 'N/A'}\n"
                f"- Excerpt: {snap.content_excerpt[:300]}\n"
            )

        if plan.search_query:
            summary_parts.append(f"\n**Search query:** {plan.search_query}\n")

        summary = "".join(summary_parts)
        ctx.add_workflow_output("browser_pipeline", summary)
        ctx.set_workflow_memory("browser_session", runtime.memory.to_dict())
        ctx.set_workflow_memory("browser_pipeline_completed", True)

        await emit_fn(
            execution_id,
            "log",
            "BrowserOperator",
            f"Browser pipeline completed — {len(runtime.memory.snapshots)} page(s) captured",
            session=runtime.memory.to_dict(),
            results=results,
        )

        return {
            "plan": plan.to_dict(),
            "results": results,
            "session": runtime.memory.to_dict(),
            "summary": summary,
        }
