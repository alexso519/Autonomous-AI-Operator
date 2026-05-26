from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.execution.context_manager import ContextManager

from app.tools.tool_execution_service import ToolExecutionService
from app.tools.tool_models import ToolCallRequest, ToolExecutionResponse

logger = logging.getLogger(__name__)

MAX_TOOL_INVOCATIONS = 3


@dataclass
class ToolInvocationOutcome:
    request: ToolCallRequest
    response: ToolExecutionResponse


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Find the first JSON object in a text blob."""
    decoder = json.JSONDecoder()
    for start_index, char in enumerate(raw_text):
        if char != "{":
            continue

        try:
            candidate, end_index = decoder.raw_decode(raw_text[start_index:])
        except json.JSONDecodeError:
            continue

        if isinstance(candidate, dict):
            return candidate

    return None


def parse_tool_request(raw_text: str) -> ToolCallRequest | None:
    payload = _extract_json_object(raw_text.strip())
    if not payload:
        return None

    if "tool_name" not in payload or "input_data" not in payload:
        return None

    try:
        request = ToolCallRequest(**payload)
    except Exception as exc:
        logger.debug("Invalid tool request payload: %s", exc)
        return None

    return request


async def execute_tool_request_if_present(
    execution_id: str,
    node_id: str,
    agent_name: str,
    raw_text: str,
    ctx: ContextManager | None = None,
) -> ToolInvocationOutcome | None:
    request = parse_tool_request(raw_text)
    if request is None:
        return None

    request.execution_id = execution_id
    request.node_id = node_id
    request.agent_name = agent_name

    service = ToolExecutionService()
    response = await service.execute_tool(request, ctx)
    return ToolInvocationOutcome(request=request, response=response)
