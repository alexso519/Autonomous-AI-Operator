"""
File workflow engine — automate file movement between applications.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class FileWorkflowEngine:
    """Manage download/upload and cross-app file workflows."""

    def __init__(self, execution_id: str, workspace_path: str = "") -> None:
        self.execution_id = execution_id
        self.workspace_path = workspace_path

    async def run_file_workflow(
        self,
        workflow_type: str,
        *,
        source_path: str = "",
        dest_path: str = "",
        content: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        workflow_id = f"fwf-{uuid.uuid4().hex[:8]}"
        result: dict[str, Any] = {"workflowId": workflow_id, "type": workflow_type, "success": False}

        try:
            from app.computer_use.file_interaction import FileInteraction
            from app.execution.sandbox_manager import SandboxManager

            ws = self.workspace_path or str(SandboxManager.get_desktop_workspace(self.execution_id).workdir)
            files = FileInteraction(ws)

            if workflow_type == "write_note":
                path = dest_path or "note.txt"
                write_result = files.write_file(path, content or "Automated note")
                result.update(write_result.to_dict() if hasattr(write_result, "to_dict") else {"written": path})
                result["success"] = True

            elif workflow_type == "copy_to_workspace":
                src = Path(source_path)
                if src.exists():
                    dest = Path(ws) / (dest_path or src.name)
                    dest.write_bytes(src.read_bytes())
                    result["destPath"] = str(dest)
                    result["success"] = True
                else:
                    result["error"] = f"Source not found: {source_path}"

            elif workflow_type == "collect_screenshots":
                shot_dir = Path(ws) / "screenshots"
                shot_dir.mkdir(exist_ok=True)
                result["screenshotDir"] = str(shot_dir)
                result["success"] = True

            elif workflow_type == "generate_report":
                report_path = Path(ws) / (dest_path or "report.md")
                report_path.write_text(content or "# Automated Report\n", encoding="utf-8")
                result["reportPath"] = str(report_path)
                result["success"] = True

            else:
                result["error"] = f"Unknown workflow type: {workflow_type}"

        except Exception as exc:
            logger.warning("File workflow failed: %s", exc)
            result["error"] = str(exc)

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "file_workflow_completed",
                "WorkflowExecutor",
                f"File workflow {workflow_type}: {'ok' if result['success'] else 'failed'}",
                workflow=result,
            )
        return result
