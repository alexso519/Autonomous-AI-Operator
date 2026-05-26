"""
Specialized autonomous coding and computer-use agent profiles.
"""

from __future__ import annotations

from typing import Any

CODING_AGENT_PROFILES: dict[str, dict[str, str]] = {
    "RepoArchitect": {
        "role": "Repository Architect",
        "label": "RepoArchitect",
        "goal_template": "Analyze repository structure and identify relevant files for: {objective}",
        "backstory": (
            "You inspect codebases, map dependencies, and identify the minimal set of "
            "files needed to accomplish coding tasks. You never guess file locations."
        ),
    },
    "CodeEditor": {
        "role": "Code Editor",
        "label": "CodeEditor",
        "goal_template": "Plan and apply safe code edits for: {objective}",
        "backstory": (
            "You generate precise edit plans and apply patches with rollback support. "
            "Every change is backed by a diff and version history."
        ),
    },
    "TestEngineer": {
        "role": "Test Engineer",
        "label": "TestEngineer",
        "goal_template": "Run tests and verify fixes for: {objective}",
        "backstory": (
            "You execute test suites, parse failures, and confirm when fixes are verified. "
            "You report exit codes and failure details precisely."
        ),
    },
    "BugInvestigator": {
        "role": "Bug Investigator",
        "label": "BugInvestigator",
        "goal_template": "Diagnose test failures and guide retry patches for: {objective}",
        "backstory": (
            "You analyze failing tests, identify root causes, and trigger confidence-aware "
            "retries with automatic rollback on regression."
        ),
    },
    "TerminalOperator": {
        "role": "Terminal Operator",
        "label": "TerminalOperator",
        "goal_template": "Execute safe terminal workflows for: {objective}",
        "backstory": (
            "You run allowlisted shell commands in isolated sandboxes with timeout "
            "enforcement and full provenance tracking."
        ),
    },
    "BrowserOperator": {
        "role": "Browser Operator",
        "label": "BrowserOperator",
        "goal_template": "Perform browser automation for: {objective}",
        "backstory": (
            "You navigate web pages, capture screenshots, extract structured content, "
            "and provide evidence-backed findings with provenance."
        ),
    },
}

CODING_ROLE_ORDER = [
    "RepoArchitect",
    "CodeEditor",
    "TestEngineer",
    "BugInvestigator",
]

COMPUTER_USE_ROLE_ORDER = [
    "TerminalOperator",
    "BrowserOperator",
    "RepoArchitect",
    "CodeEditor",
    "TestEngineer",
    "BugInvestigator",
]


def is_coding_objective(objective: str) -> bool:
    from app.coding.coding_orchestrator import CodingOrchestrator
    return CodingOrchestrator.is_coding_objective(objective)


def is_browser_objective(objective: str) -> bool:
    from app.browser.browser_orchestrator import BrowserOrchestrator
    return BrowserOrchestrator.is_browser_objective(objective)


def is_computer_use_objective(objective: str) -> bool:
    return is_coding_objective(objective) or is_browser_objective(objective)


def is_computer_use_pipeline_objective(objective: str) -> bool:
    from app.computer_use.computer_use_orchestrator import ComputerUseOrchestrator
    return ComputerUseOrchestrator.is_computer_use_objective(objective)


def apply_coding_profiles(
    nodes: list[dict[str, Any]],
    objective: str,
) -> list[dict[str, Any]]:
    """Map workflow agent nodes to specialized coding/computer-use roles."""
    if not is_computer_use_objective(objective):
        return nodes

    role_order = (
        COMPUTER_USE_ROLE_ORDER
        if is_browser_objective(objective)
        else CODING_ROLE_ORDER
    )

    updated = []
    agent_idx = 0

    for node in nodes:
        if node.get("type") == "approval" or node.get("data", {}).get("nodeType") == "approval":
            updated.append(node)
            continue

        if agent_idx < len(role_order):
            profile_key = role_order[agent_idx]
            profile = CODING_AGENT_PROFILES[profile_key]
            data = dict(node.get("data") or {})
            data["label"] = profile["label"]
            data["role"] = profile["role"]
            data["goal"] = profile["goal_template"].format(objective=objective[:300])
            data["backstory"] = profile["backstory"]
            data["executionMetadata"] = {
                **(data.get("executionMetadata") or {}),
                "codingAgent": profile_key,
                "createdBy": "CodingAgents",
            }
            node = {**node, "data": data}

        updated.append(node)
        agent_idx += 1

    return updated
