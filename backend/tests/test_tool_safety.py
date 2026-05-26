"""Tests for tool safety policy and auto-approval."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.tool_execution_service import ToolExecutionService
from app.tools.tool_models import ToolCallRequest
from app.tools.tool_safety import (
    is_auto_approved,
    requires_manual_approval,
    SAFE_AUTO_APPROVE_TOOLS,
    RESTRICTED_APPROVAL_TOOLS,
)


def test_safe_tools_auto_approved():
    for name in SAFE_AUTO_APPROVE_TOOLS:
        assert is_auto_approved(name)
        assert not requires_manual_approval(name)


def test_restricted_tools_require_approval():
    for name in RESTRICTED_APPROVAL_TOOLS:
        assert requires_manual_approval(name)
        assert not is_auto_approved(name)


async def test_calculator_runs_without_approval():
    service = ToolExecutionService()
    response = await service.execute_tool(
        ToolCallRequest(tool_name="calculator", input_data={"expression": "10 + 5"})
    )
    assert response.status == "completed"
    assert response.output["result"] == 15


async def test_shell_command_requires_approval():
    service = ToolExecutionService()
    response = await service.execute_tool(
        ToolCallRequest(tool_name="shell_command", input_data={"command": "echo hello", "args": []})
    )
    assert response.status == "pending_approval"
    assert response.approval_payload is not None


async def test_file_write_requires_approval():
    service = ToolExecutionService()
    response = await service.execute_tool(
        ToolCallRequest(
            tool_name="file_write",
            input_data={"path": "data/test.txt", "content": "hello"},
        )
    )
    assert response.status == "pending_approval"


if __name__ == "__main__":
    test_safe_tools_auto_approved()
    test_restricted_tools_require_approval()
    asyncio.run(test_calculator_runs_without_approval())
    asyncio.run(test_shell_command_requires_approval())
    asyncio.run(test_file_write_requires_approval())
    print("All tool safety tests passed.")
