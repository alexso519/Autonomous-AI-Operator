from copy import deepcopy
from typing import Any

from app.tools.tool_models import JsonTransformInput, ToolExecutionError


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    keys = path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise ToolExecutionError(f"JSON path not found: {path}")
        current = current[key]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _delete_path(data: dict[str, Any], path: str) -> None:
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if not isinstance(current, dict) or key not in current:
            raise ToolExecutionError(f"JSON path not found: {path}")
        current = current[key]
    if keys[-1] not in current:
        raise ToolExecutionError(f"JSON path not found: {path}")
    del current[keys[-1]]


def transform_json(input_model: JsonTransformInput) -> dict[str, Any]:
    data = deepcopy(input_model.data)
    original = deepcopy(data)

    for operation in input_model.operations:
        if operation.op == "copy":
            value = _resolve_path(data, operation.source)
            if operation.target is None:
                raise ToolExecutionError("Target path is required for copy operations.")
            _set_path(data, operation.target, value)
        elif operation.op == "rename":
            if operation.target is None:
                raise ToolExecutionError("Target path is required for rename operations.")
            value = _resolve_path(data, operation.source)
            _set_path(data, operation.target, value)
            _delete_path(data, operation.source)
        elif operation.op == "remove":
            _delete_path(data, operation.source)
        elif operation.op == "extract":
            value = _resolve_path(data, operation.source)
            if operation.target is None:
                raise ToolExecutionError("Target path is required for extract operations.")
            _set_path(data, operation.target, value)
        else:
            raise ToolExecutionError(f"Unsupported JSON transform operation: {operation.op}")

    transformed = data != original
    return {"result": data, "transformed": transformed}
