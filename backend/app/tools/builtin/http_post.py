import httpx
from typing import Any

from app.tools.tool_models import HttpPostInput, ToolExecutionError


def execute_http_post(input_model: HttpPostInput) -> dict[str, Any]:
    if not input_model.json_body and not input_model.text_body:
        raise ToolExecutionError("HTTP POST must include either json_body or text_body.")

    try:
        response = httpx.post(
            input_model.url,
            headers=input_model.headers or {},
            json=input_model.json_body,
            content=input_model.text_body,
            timeout=input_model.timeout_seconds,
            follow_redirects=False,
        )
    except Exception as exc:
        raise ToolExecutionError(f"HTTP POST request failed: {exc}") from exc

    return {
        "status_code": response.status_code,
        "response_headers": dict(response.headers),
        "response_body": response.text,
    }
