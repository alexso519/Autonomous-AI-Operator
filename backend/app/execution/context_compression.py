"""
Deterministic context compression for local LLM compatibility.

Older agent outputs are compressed into short summaries while the most
recent outputs are kept at full fidelity. Tool results are always preserved.

Note: Prefer ContextBudgetManager for new code — this module provides
low-level compression primitives used by the unified memory architecture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextBudget:
    """Configurable context size limits."""

    full_output_count: int = 2
    """Number of most recent agent outputs kept at full length."""

    max_full_output_chars: int = 1200
    """Max chars per recent full output."""

    max_compressed_output_chars: int = 350
    """Max chars per compressed older output."""

    max_total_context_chars: int = 3800
    """Hard cap on the combined context block."""

    max_tool_result_chars: int = 800
    """Max chars for aggregated tool results section."""


def _is_retry_agent(name: str) -> bool:
    lower = name.lower()
    return lower.startswith("retry ") or " retry " in lower


def _compress_text(text: str, max_chars: int) -> str:
    """Deterministically shrink text to a short summary."""
    cleaned = text.strip()
    if not cleaned:
        return "(empty)"

    if len(cleaned) <= max_chars:
        return cleaned

    # Prefer markdown headings as section anchors
    headings = re.findall(r"^#{1,3}\s+(.+)$", cleaned, re.MULTILINE)
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]

    parts: list[str] = []
    if headings:
        parts.append("Sections: " + "; ".join(headings[:4]))

    if paragraphs:
        parts.append(paragraphs[0][:120])
        if len(paragraphs) > 1:
            parts.append("… " + paragraphs[-1][:120])

    summary = " | ".join(parts) if parts else cleaned[:max_chars]
    if len(summary) > max_chars:
        summary = summary[: max_chars - 18] + "... [compressed]"
    return summary


def _format_tool_results(tool_calls: list[dict[str, Any]], max_chars: int) -> str:
    if not tool_calls:
        return ""

    lines = ["Tool Results (ground truth — prefer over guesses):"]
    for call in tool_calls[-6:]:
        if call.get("status") != "completed":
            continue
        name = call.get("tool_name", "unknown")
        output = call.get("output") or {}
        if name == "web_search":
            results = output.get("results") or []
            for hit in results[:3]:
                lines.append(
                    f"- [{hit.get('title', '')}] {hit.get('snippet', '')[:200]}"
                )
        elif name == "webpage_fetch":
            lines.append(f"- Fetched {output.get('url', '')}: {str(output.get('text', ''))[:300]}")
        else:
            lines.append(f"- {name}: {json.dumps(output, ensure_ascii=False)[:200]}")

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 20] + "\n... [tool results truncated]"
    return block


def build_compressed_context(
    initial_context: str,
    outputs: dict[str, dict[str, Any]],
    tool_calls: list[dict[str, Any]] | None,
    budget: ContextBudget | None = None,
) -> str:
    """
    Build a context string with compression applied to older outputs.

    Args:
        initial_context: Original workflow objective/context.
        outputs: node_id -> {agent_name, output, metadata} mapping.
        tool_calls: Completed tool call records from shared memory.
        budget: Optional override for context limits.
    """
    budget = budget or ContextBudget()
    parts: list[str] = []

    if initial_context.strip():
        parts.append(f"Workflow Context:\n{initial_context.strip()[:800]}")

    tool_block = _format_tool_results(tool_calls or [], budget.max_tool_result_chars)
    if tool_block:
        parts.append(tool_block)

    if outputs:
        entries = list(outputs.values())
        # Drop retry agent outputs from context — they duplicate primary work
        primary = [e for e in entries if not _is_retry_agent(e.get("agent_name", ""))]
        if not primary:
            primary = entries

        recent = primary[-budget.full_output_count :]
        older = primary[: -budget.full_output_count] if len(primary) > budget.full_output_count else []

        if older:
            parts.append("\nEarlier Agent Summaries:")
            for entry in older:
                name = entry.get("agent_name", "Agent")
                compressed = _compress_text(
                    entry.get("output", ""),
                    budget.max_compressed_output_chars,
                )
                parts.append(f"[{name}]: {compressed}")

        if recent:
            parts.append("\nRecent Agent Outputs:")
            for entry in recent:
                name = entry.get("agent_name", "Agent")
                raw = entry.get("output", "")
                if len(raw) > budget.max_full_output_chars:
                    raw = raw[: budget.max_full_output_chars] + "... [truncated]"
                parts.append(f"[{name}]: {raw}")

    combined = "\n\n".join(parts)
    if len(combined) > budget.max_total_context_chars:
        combined = combined[: budget.max_total_context_chars] + "\n... [context truncated]"
    return combined
