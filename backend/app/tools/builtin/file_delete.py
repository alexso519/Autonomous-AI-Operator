from pathlib import Path
from typing import Any

from app.tools.permissions import validate_delete_path
from app.tools.tool_models import FileDeleteInput, ToolExecutionError


def delete_file(input_model: FileDeleteInput) -> dict[str, Any]:
    resolved_path = validate_delete_path(input_model.path)
    if not resolved_path.exists():
        raise ToolExecutionError(f"File not found: {resolved_path}")
    if not resolved_path.is_file():
        raise ToolExecutionError(f"Only files can be deleted: {resolved_path}")

    try:
        resolved_path.unlink()
    except Exception as exc:
        raise ToolExecutionError(f"Failed to delete file: {exc}") from exc

    return {
        "path": str(resolved_path),
        "deleted": True,
    }
