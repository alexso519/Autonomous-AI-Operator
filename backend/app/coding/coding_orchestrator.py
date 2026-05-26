"""
Coding orchestrator — autonomous coding pipeline with repo analysis and debug loops.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.coding.code_planner import CodePlanner
from app.coding.debug_loop import DebugLoop
from app.coding.repo_analyzer import RepoAnalyzer
from app.coding.test_runner import TestRunner
from app.execution.context_manager import ContextManager
from app.execution.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CodingOrchestrator:
    """Plan and execute autonomous coding workflows."""

    PIPELINE_NAME = "autonomous_coding_pipeline"

    @classmethod
    def is_coding_objective(cls, objective: str) -> bool:
        lower = objective.lower()
        keywords = (
            "fix", "debug", "code", "implement", "refactor", "patch",
            "test", "failing", "repository", "repo", "file", "bug",
            "compile", "build", "lint", "pytest", "unit test",
        )
        return any(k in lower for k in keywords)

    @classmethod
    async def execute_coding_pipeline(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        """Run full coding pipeline: analyze → plan → debug loop → verify."""
        root = repo_root or PROJECT_ROOT

        await emit_fn(
            execution_id,
            "log",
            "RepoArchitect",
            f"Starting coding pipeline for: {objective[:100]}",
            pipeline=cls.PIPELINE_NAME,
        )

        ws = SandboxManager.get_or_create(execution_id, clone_from=root)
        SandboxManager.checkpoint(execution_id, "pre_coding")

        visual_context = ctx.get_workflow_memory("visual_context")
        if visual_context:
            ctx.set_workflow_memory("coding_visual_context", visual_context)

        analysis = RepoAnalyzer.analyze(ws.workdir, task_hint=objective)
        ctx.set_workflow_memory("repo_analysis", analysis.to_dict())

        await emit_fn(
            execution_id,
            "log",
            "RepoArchitect",
            f"Indexed {analysis.code_files} source files, {analysis.test_files} tests",
            repoAnalysis=analysis.to_dict(),
        )

        plan = CodePlanner.plan(analysis, objective)
        ctx.set_workflow_memory("edit_plan", plan.to_dict())

        await emit_fn(
            execution_id,
            "patch_generated",
            "CodeEditor",
            f"Generated edit plan {plan.plan_id}",
            plan=plan.to_dict(),
        )

        debug_result = await DebugLoop.run(
            ws.workdir,
            objective,
            execution_id=execution_id,
            emit_fn=emit_fn,
            initial_analysis=analysis,
        )

        ctx.set_workflow_memory("debug_loop_result", debug_result.to_dict())
        ctx.set_workflow_memory("patch_history", debug_result.patch_history)

        if not debug_result.success:
            test_cmd = plan.test_command
            final_test = await TestRunner.run(
                ws.workdir, test_cmd,
                execution_id=execution_id,
                emit_fn=emit_fn,
            )
            debug_result.final_test = final_test

        summary = (
            f"## Coding Pipeline Summary\n\n"
            f"- Files analyzed: {analysis.total_files}\n"
            f"- Test files: {analysis.test_files}\n"
            f"- Debug loop: {'PASSED' if debug_result.success else 'INCOMPLETE'}\n"
            f"- Iterations: {len(debug_result.iterations)}\n"
            f"- Patches applied: {len(debug_result.patch_history)}\n\n"
            f"{debug_result.summary}\n"
        )

        ctx.add_workflow_output("coding_pipeline", summary)
        ctx.set_workflow_memory("coding_pipeline_completed", True)

        await emit_fn(
            execution_id,
            "log",
            "RepoArchitect",
            debug_result.summary,
            pipelineResult=debug_result.to_dict(),
        )

        return debug_result.to_dict()
