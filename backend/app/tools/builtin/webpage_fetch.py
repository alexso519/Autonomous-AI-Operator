"""Sandboxed read-only webpage fetch (GET only)."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from app.tools.permissions import validate_http_get_url
from app.tools.tool_models import ToolExecutionError, WebpageFetchInput

_USER_AGENT = "AutonomousAI-Operator/0.1 (research; +local-first)"


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def execute_webpage_fetch(input_model: WebpageFetchInput) -> dict[str, Any]:
    url = validate_http_get_url(input_model.url.strip())

    try:
        response = httpx.get(
            url,
            timeout=input_model.timeout_seconds,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
    except Exception as exc:
        raise ToolExecutionError(f"Webpage fetch failed: {exc}") from exc

    content_type = response.headers.get("content-type", "unknown")
    raw = response.content[: input_model.max_bytes]
    truncated = len(response.content) > input_model.max_bytes

    if "html" in content_type.lower():
        text = _html_to_text(raw.decode("utf-8", errors="replace"))
    else:
        text = raw.decode("utf-8", errors="replace")

    if len(text) > input_model.max_bytes:
        text = text[: input_model.max_bytes]
        truncated = True

    return {
        "url": url,
        "status_code": response.status_code,
        "content_type": content_type,
        "text": text,
        "truncated": truncated,
    }
