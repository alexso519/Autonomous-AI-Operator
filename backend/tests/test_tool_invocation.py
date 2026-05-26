"""
Tests for agent tool invocation parsing and helper execution.
Run with: python -m pytest backend/tests/test_tool_invocation.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.tool_invocation import (
    execute_tool_request_if_present,
    parse_tool_request,
)


def test_parse_tool_request_valid_json():
    text = '{"tool_name": "calculator", "input_data": {"expression": "2 + 2"}}'
    request = parse_tool_request(text)

    assert request is not None
    assert request.tool_name == "calculator"
    assert request.input_data["expression"] == "2 + 2"


def test_parse_tool_request_ignores_non_tool_content():
    text = "Here is a summary: {\"not_a_tool\": true}"
    request = parse_tool_request(text)

    assert request is None


async def test_execute_tool_request_if_present_calculator():
    outcome = await execute_tool_request_if_present(
        execution_id=None,
        node_id="node_tool_1",
        agent_name="Calculator Agent",
        raw_text='{"tool_name": "calculator", "input_data": {"expression": "6 * 7"}}',
        ctx=None,
    )

    assert outcome is not None
    assert outcome.response.status == "completed"
    assert outcome.response.tool_name == "calculator"
    assert outcome.response.output["result"] == 42


if __name__ == "__main__":
    asyncio.run(test_execute_tool_request_if_present_calculator())
