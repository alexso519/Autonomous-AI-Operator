from pathlib import Path
from typing import Any

from app.tools.permissions import validate_write_path
from app.tools.tool_models import FileWriteInput, ToolExecutionError


def write_file(input_model: FileWriteInput) -> dict[str, Any]:
    resolved_path = validate_write_path(input_model.path)
    if resolved_path.exists() and resolved_path.is_dir():
        raise ToolExecutionError(f"Target path is a directory: {resolved_path}")

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    mode = input_model.mode
    try:
        written = resolved_path.write_text(input_model.content, encoding="utf-8")
    except Exception as exc:
        raise ToolExecutionError(f"Failed to write file: {exc}") from exc

    return {
        "path": str(resolved_path),
        "bytes_written": len(input_model.content.encode("utf-8")),
    }
