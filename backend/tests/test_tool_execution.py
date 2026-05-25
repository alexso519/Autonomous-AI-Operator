"""
Basic tests for tool execution components.
Run with: python -m pytest backend/tests/test_tool_execution.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.execution.context_manager import ContextManager
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.tool_models import ToolCallRequest
from app.tools.tool_registry import tool_registry


async def test_calculator_tool_execution():
    service = ToolExecutionService()
    request = ToolCallRequest(
        tool_name="calculator",
        input_data={"expression": "2 + 3 * 4"},
    )
    response = await service.execute_tool(request)

    assert response.status == "completed"
    assert response.tool_name == "calculator"
    assert response.output["result"] == 14


async def test_json_transform_tool_execution():
    service = ToolExecutionService()
    request = ToolCallRequest(
        tool_name="json_transform",
        input_data={
            "data": {"person": {"name": "Alice", "age": 30}},
            "operations": [
                {"op": "copy", "source": "person.name", "target": "name"},
                {"op": "remove", "source": "person.age"},
            ],
        },
    )
    response = await service.execute_tool(request)

    assert response.status == "completed"
    assert response.output["result"]["name"] == "Alice"
    assert "age" not in response.output["result"]["person"]


async def test_file_reader_sandbox():
    base = Path(__file__).parent.parent
    temp_path = base / "data" / "tool_reader_test.txt"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_text("hello tool layer", encoding="utf-8")

    service = ToolExecutionService()
    request = ToolCallRequest(
        tool_name="file_reader",
        input_data={"path": str(temp_path), "max_bytes": 100},
    )
    response = await service.execute_tool(request)

    assert response.status == "completed"
    assert "hello tool layer" in response.output["content"]


async def test_shell_command_requires_approval():
    service = ToolExecutionService()
    request = ToolCallRequest(
        tool_name="shell_command",
        input_data={"command": "echo hello", "args": []},
    )
    response = await service.execute_tool(request)

    assert response.status == "pending_approval"
    assert response.approval_payload is not None
    assert response.approval_payload["tool_name"] == "shell_command"


if __name__ == "__main__":
    asyncio.run(test_calculator_tool_execution())
    asyncio.run(test_json_transform_tool_execution())
    asyncio.run(test_file_reader_sandbox())
    asyncio.run(test_shell_command_requires_approval())
