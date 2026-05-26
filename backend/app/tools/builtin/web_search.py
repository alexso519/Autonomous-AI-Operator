"""Sandboxed web search using DuckDuckGo (no API key required)."""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.tools.tool_models import ToolExecutionError, WebSearchInput

_USER_AGENT = "AutonomousAI-Operator/0.1 (research; +local-first)"
_TIMEOUT = 12


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML lite results deterministically."""
    results: list[dict[str, str]] = []
    # Result blocks: <a class="result__a" href="...">title</a> ... <a class="result__snippet">snippet</a>
    link_pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    snippet_pattern = re.compile(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
        re.I | re.S,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for idx, (url, title_html) in enumerate(links[:max_results]):
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippets[idx]) if idx < len(snippets) else ""
        if not title:
            continue
        # DuckDuckGo redirect URLs — keep as-is for traceability
        results.append({"title": title[:200], "url": url[:500], "snippet": snippet[:400]})

    return results


def _fetch_instant_answer(query: str) -> list[dict[str, str]]:
    """DuckDuckGo Instant Answer API — fast factual snippets."""
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1"
    try:
        response = httpx.get(url, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise ToolExecutionError(f"Instant answer lookup failed: {exc}") from exc

    results: list[dict[str, str]] = []
    abstract = (data.get("AbstractText") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    if abstract:
        results.append({
            "title": data.get("Heading") or query,
            "url": abstract_url or "https://duckduckgo.com/",
            "snippet": abstract[:600],
        })

    for topic in data.get("RelatedTopics") or []:
        if len(results) >= 5:
            break
        if isinstance(topic, dict) and topic.get("Text"):
            results.append({
                "title": topic.get("Text", "")[:120],
                "url": topic.get("FirstURL") or "",
                "snippet": topic.get("Text", "")[:400],
            })

    return results


def execute_web_search(input_model: WebSearchInput) -> dict[str, Any]:
    query = input_model.query.strip()
    max_results = input_model.max_results

    results: list[dict[str, str]] = []

    # 1. Instant Answer API for quick factual grounding
    try:
        results.extend(_fetch_instant_answer(query))
    except ToolExecutionError:
        pass

    # 2. HTML lite search for broader coverage
    if len(results) < max_results:
        search_url = "https://html.duckduckgo.com/html/"
        try:
            response = httpx.post(
                search_url,
                data={"q": query},
                timeout=_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
            response.raise_for_status()
            html_results = _parse_ddg_html(response.text, max_results)
            seen_urls = {r["url"] for r in results}
            for hit in html_results:
                if hit["url"] not in seen_urls:
                    results.append(hit)
                    seen_urls.add(hit["url"])
                if len(results) >= max_results:
                    break
        except Exception as exc:
            if not results:
                raise ToolExecutionError(f"Web search failed: {exc}") from exc

    if not results:
        results.append({
            "title": "No results",
            "url": "",
            "snippet": f"No web results found for '{query}'. Try refining the query.",
        })

    return {
        "query": query,
        "results": results[:max_results],
        "source": "duckduckgo",
    }
