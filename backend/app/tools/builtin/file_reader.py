from pathlib import Path
from typing import Any

from app.tools.permissions import validate_read_path
from app.tools.tool_models import FileReaderInput, ToolExecutionError


def execute_file_read(input_model: FileReaderInput) -> dict[str, Any]:
    resolved_path = validate_read_path(input_model.path)
    if not resolved_path.exists():
        raise ToolExecutionError(f"File not found: {resolved_path}")
    if not resolved_path.is_file():
        raise ToolExecutionError(f"Path is not a file: {resolved_path}")

    try:
        raw = resolved_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise ToolExecutionError(f"Failed to read file: {exc}") from exc

    if input_model.max_bytes < 0:
        raise ToolExecutionError("max_bytes must be a non-negative integer.")

    truncated = len(raw.encode("utf-8")) > input_model.max_bytes
    content = raw[: input_model.max_bytes]

    return {
        "path": str(resolved_path),
        "content": content,
        "truncated": truncated,
    }
