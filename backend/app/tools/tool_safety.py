"""
Deterministic tool safety classification and approval policy.

Safe tools execute automatically with a full audit trail.
Restricted tools pause for human approval before execution.
"""

from __future__ import annotations

from typing import Literal

ToolSafetyLevel = Literal["safe", "restricted"]

# ── Explicit tool sets (source of truth) ──────────────────────────

SAFE_AUTO_APPROVE_TOOLS: frozenset[str] = frozenset({
    "calculator",
    "markdown_generator",
    "json_transform",
    "structured_data_extractor",
    "file_reader",
    "web_search",
    "webpage_fetch",
})

RESTRICTED_APPROVAL_TOOLS: frozenset[str] = frozenset({
    "shell_command",
    "file_write",
    "file_delete",
    "http_post",
})


def get_tool_safety_level(tool_name: str) -> ToolSafetyLevel:
    """Return the safety level for a registered tool name."""
    if tool_name in SAFE_AUTO_APPROVE_TOOLS:
        return "safe"
    if tool_name in RESTRICTED_APPROVAL_TOOLS:
        return "restricted"
    # Unknown tools default to restricted — fail closed.
    return "restricted"


def requires_manual_approval(tool_name: str) -> bool:
    """True when the tool must pause for human approval before running."""
    return get_tool_safety_level(tool_name) == "restricted"


def is_auto_approved(tool_name: str) -> bool:
    """True when the tool may run without human approval."""
    return get_tool_safety_level(tool_name) == "safe"


def permission_level_for(tool_name: str) -> str:
    """Map tool name to registry permission_level string."""
    level = get_tool_safety_level(tool_name)
    if level == "safe":
        # file_reader is safe but read-only restricted path access
        if tool_name == "file_reader":
            return "restricted"
        return "safe"
    return "approval_required"
