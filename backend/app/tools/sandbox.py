import asyncio
from typing import Any

from app.tools.permissions import validate_tool_request
from app.tools.tool_models import (
    ToolBaseInput,
    ToolDefinition,
    ToolApprovalRequired,
    ToolExecutionError,
)
from app.tools.builtin import (
    calculate_expression,
    execute_file_read,
    generate_markdown,
    extract_structured_data,
    transform_json,
    execute_shell_command,
    write_file,
    delete_file,
    execute_http_post,
)


def _executor_for_tool(name: str):
    return {
        "calculator": calculate_expression,
        "json_transform": transform_json,
        "markdown_generator": generate_markdown,
        "structured_data_extractor": extract_structured_data,
        "file_reader": execute_file_read,
        "shell_command": execute_shell_command,
        "file_write": write_file,
        "file_delete": delete_file,
        "http_post": execute_http_post,
    }.get(name)


async def execute_tool(
    definition: ToolDefinition,
    input_obj: ToolBaseInput,
    approved: bool = False,
) -> dict[str, Any]:
    if definition.requires_approval and not approved:
        raise ToolApprovalRequired(
            tool_name=definition.name,
            reason=f"Approval required for tool '{definition.name}'.",
            approval_payload={
                "tool_name": definition.name,
                "input": input_obj.model_dump(),
                "permission_level": definition.permission_level,
            },
        )

    validate_tool_request(definition, input_obj)
    executor = _executor_for_tool(definition.name)
    if executor is None:
        raise ToolExecutionError(f"No executor available for tool '{definition.name}'.")

    try:
        result = await asyncio.to_thread(executor, input_obj)
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(
            f"Tool '{definition.name}' execution failed: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise ToolExecutionError(
            f"Tool '{definition.name}' returned unexpected result type: {type(result)}"
        )

    return result
