"""Tests for web research tools (mocked HTTP)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.builtin.web_search import execute_web_search
from app.tools.builtin.webpage_fetch import execute_webpage_fetch
from app.tools.tool_models import WebSearchInput, WebpageFetchInput


def test_web_search_returns_results():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "AbstractText": "NVIDIA makes GPUs.",
        "AbstractURL": "https://example.com",
        "Heading": "NVIDIA",
        "RelatedTopics": [],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.tools.builtin.web_search.httpx.get", return_value=mock_response):
        with patch("app.tools.builtin.web_search.httpx.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            mock_post.return_value.text = ""
            result = execute_web_search(WebSearchInput(query="NVIDIA GPUs", max_results=3))

    assert result["query"] == "NVIDIA GPUs"
    assert len(result["results"]) >= 1
    assert "NVIDIA" in result["results"][0]["snippet"]


def test_webpage_fetch_extracts_text():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.content = b"<html><body><p>Hello world</p></body></html>"

    with patch("app.tools.builtin.webpage_fetch.httpx.get", return_value=mock_response):
        result = execute_webpage_fetch(
            WebpageFetchInput(url="https://example.com/page", max_bytes=4096)
        )

    assert result["status_code"] == 200
    assert "Hello world" in result["text"]


if __name__ == "__main__":
    test_web_search_returns_results()
    test_webpage_fetch_extracts_text()
    print("Web tool tests passed.")
