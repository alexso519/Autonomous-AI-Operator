"""
Normalize common LLM tool-input variants before Pydantic validation.
"""

from __future__ import annotations

from typing import Any


def _first_string(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item).strip()
            if text:
                return text[:500]
        return ""
    return str(value).strip()[:500]


def _normalize_web_search_input(data: dict[str, Any]) -> dict[str, Any]:
    query = data.get("query")
    if isinstance(query, list):
        query = _first_string(query)
    elif query is not None:
        query = str(query).strip()[:500]

    if not query:
        for key in ("queries", "search_query", "search", "q", "text", "keywords"):
            if key not in data:
                continue
            query = _first_string(data[key])
            if query:
                break

    result: dict[str, Any] = {}
    if query:
        result["query"] = query
    if "max_results" in data:
        result["max_results"] = data["max_results"]
    return result


def _normalize_webpage_fetch_input(data: dict[str, Any]) -> dict[str, Any]:
    url = data.get("url")
    if isinstance(url, list):
        url = _first_string(url)
    elif url is not None:
        url = str(url).strip()

    if not url:
        for key in ("urls", "link", "href", "uri"):
            if key not in data:
                continue
            url = _first_string(data[key])
            if url:
                break

    result: dict[str, Any] = {}
    if url:
        result["url"] = url
    if "max_bytes" in data:
        result["max_bytes"] = data["max_bytes"]
    if "timeout_seconds" in data:
        result["timeout_seconds"] = data["timeout_seconds"]
    return result


def normalize_tool_input(tool_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
    """Map alternate LLM field names/shapes to each tool's expected schema."""
    if not input_data:
        return {}

    if tool_name == "web_search":
        return _normalize_web_search_input(input_data)
    if tool_name == "webpage_fetch":
        return _normalize_webpage_fetch_input(input_data)

    return dict(input_data)
