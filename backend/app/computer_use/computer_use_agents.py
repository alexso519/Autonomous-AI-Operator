"""
Computer-use agent profiles and objective detection.
"""

from __future__ import annotations

COMPUTER_USE_AGENT_PROFILES: dict[str, dict[str, str]] = {
    "DesktopNavigator": {
        "label": "Desktop Navigator",
        "role": "DesktopNavigator",
        "goal_template": "Navigate the desktop environment to accomplish: {objective}",
        "backstory": "Expert at window management, app launching, and multi-step desktop navigation.",
    },
    "VisualAnalyst": {
        "label": "Visual Analyst",
        "role": "VisualAnalyst",
        "goal_template": "Analyze screen content via OCR and visual grounding for: {objective}",
        "backstory": "Specialist in screenshot reasoning, UI element detection, and visual state analysis.",
    },
    "UIOperator": {
        "label": "UI Operator",
        "role": "UIOperator",
        "goal_template": "Execute precise mouse and keyboard interactions for: {objective}",
        "backstory": "Handles click targets, form input, scrolling, and input throttling with safety checks.",
    },
    "WorkflowExecutor": {
        "label": "Workflow Executor",
        "role": "WorkflowExecutor",
        "goal_template": "Execute the full desktop workflow: {objective}",
        "backstory": "Orchestrates multi-step perception-action cycles with validation and recovery.",
    },
    "InteractionDebugger": {
        "label": "Interaction Debugger",
        "role": "InteractionDebugger",
        "goal_template": "Diagnose and recover from failed UI interactions for: {objective}",
        "backstory": "Reflects on failures, suggests recovery strategies, and prevents repeated errors.",
    },
}

COMPUTER_USE_ROLE_ORDER = [
    "VisualAnalyst",
    "DesktopNavigator",
    "UIOperator",
    "WorkflowExecutor",
    "InteractionDebugger",
]

DESKTOP_KEYWORDS = (
    "desktop", "screen", "click", "mouse", "keyboard", "window",
    "application", "app", "notepad", "calculator", "file picker",
    "clipboard", "screenshot", "ocr", "visual", "ui element",
    "computer use", "computer-use", "native app", "open app",
    "double-click", "right-click", "scroll", "type into",
)

VISUAL_KEYWORDS = (
    "screenshot", "ocr", "visual", "screen capture", "what's on screen",
    "read the screen", "find button", "locate", "grounding",
)


def is_desktop_objective(objective: str) -> bool:
    """True when objective requires native desktop interaction."""
    lower = objective.lower()
    return any(k in lower for k in DESKTOP_KEYWORDS)


def is_visual_grounding_objective(objective: str) -> bool:
    lower = objective.lower()
    return any(k in lower for k in VISUAL_KEYWORDS)


WORKFLOW_KEYWORDS = (
    "multi-step", "multi step", "long-running", "long running",
    "workflow", "task chain", "cross-app", "cross app",
    "then open", "download and", "spreadsheet", "automate",
    "persistent session", "resume workflow",
)


def is_computer_workflow_objective(objective: str) -> bool:
    """True when a long-running desktop workflow should run."""
    from app.config.settings import settings
    if not settings.enable_computer_use:
        return False
    if not is_computer_use_pipeline_objective(objective):
        return False
    lower = objective.lower()
    return any(k in lower for k in WORKFLOW_KEYWORDS) or lower.count(" then ") >= 2


def is_computer_use_pipeline_objective(objective: str) -> bool:
    """True when the full computer-use pipeline should run."""
    from app.config.settings import settings
    if not settings.enable_computer_use:
        return False
    return is_desktop_objective(objective) or is_visual_grounding_objective(objective)


def select_agent_for_step(action: str) -> str:
    mapping = {
        "observe": "VisualAnalyst",
        "analyze": "VisualAnalyst",
        "validate": "VisualAnalyst",
        "click": "UIOperator",
        "type": "UIOperator",
        "scroll": "UIOperator",
        "launch": "DesktopNavigator",
        "navigate": "DesktopNavigator",
    }
    return mapping.get(action, "WorkflowExecutor")
