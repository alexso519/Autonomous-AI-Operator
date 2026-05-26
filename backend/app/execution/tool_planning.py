"""
Tool-oriented agent planning — infer required tools before execution.

Injects recommended tool lists into agent context and persists plans
into shared memory for quality scoring and observability.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.execution.context_manager import ContextManager
from app.tools.tool_registry import tool_registry

logger = logging.getLogger(__name__)


@dataclass
class ToolPlan:
    node_id: str
    agent_name: str
    recommended_tools: list[str]
    required_tools: list[str]
    rationale: str
    is_research_task: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "agentName": self.agent_name,
            "recommendedTools": self.recommended_tools,
            "requiredTools": self.required_tools,
            "rationale": self.rationale,
            "isResearchTask": self.is_research_task,
        }


class ToolPlanner:
    """Infer and inject tool plans for workflow nodes."""

    RESEARCH_KEYWORDS = re.compile(
        r"\b(research|investigate|find|lookup|search|current|latest|news|"
        r"compare|analyze|market|competitor|web|online|external|verify|fact)\b",
        re.IGNORECASE,
    )
    WRITE_KEYWORDS = re.compile(
        r"\b(write|draft|document|report|markdown|format|compose|summarize)\b",
        re.IGNORECASE,
    )
    DATA_KEYWORDS = re.compile(
        r"\b(extract|parse|transform|json|calculate|compute|analyze data)\b",
        re.IGNORECASE,
    )
    FILE_KEYWORDS = re.compile(
        r"\b(read file|file content|load file|open file)\b",
        re.IGNORECASE,
    )
    CODING_KEYWORDS = re.compile(
        r"\b(fix|debug|code|implement|refactor|patch|test|failing|repository|"
        r"repo|bug|pytest|unit test|lint)\b",
        re.IGNORECASE,
    )
    BROWSER_KEYWORDS = re.compile(
        r"\b(browser|website|navigate|click|github|screenshot|form|web page)\b",
        re.IGNORECASE,
    )

    FACTUAL_TOOLS = ["web_search", "webpage_fetch"]
    RESEARCH_TOOL_CHAIN = ["web_search", "webpage_fetch", "structured_data_extractor"]
    WRITE_TOOLS = ["markdown_generator"]
    DATA_TOOLS = ["structured_data_extractor", "json_transform", "calculator"]
    FILE_TOOLS = ["file_reader"]
    CODING_TOOLS = ["file_reader", "file_write", "shell_command"]

    @classmethod
    def infer_tools_for_node(
        cls,
        node: dict[str, Any],
        objective: str = "",
    ) -> ToolPlan:
        node_id = node.get("id", "unknown")
        node_data = node.get("data", {})
        agent_name = node_data.get("label", "Agent")
        goal = node_data.get("goal", "")
        combined = f"{goal} {objective}".strip()

        recommended: list[str] = []
        required: list[str] = []
        rationale_parts: list[str] = []

        is_research = bool(cls.RESEARCH_KEYWORDS.search(combined))
        is_write = bool(cls.WRITE_KEYWORDS.search(combined))
        is_data = bool(cls.DATA_KEYWORDS.search(combined))
        is_file = bool(cls.FILE_KEYWORDS.search(combined))
        is_coding = bool(cls.CODING_KEYWORDS.search(combined))
        is_browser = bool(cls.BROWSER_KEYWORDS.search(combined))

        if is_research:
            recommended.extend(cls.RESEARCH_TOOL_CHAIN)
            required.extend(cls.FACTUAL_TOOLS)
            rationale_parts.append("Research task — factual tools prioritized")

        if is_write:
            for t in cls.WRITE_TOOLS:
                if t not in recommended:
                    recommended.append(t)
            rationale_parts.append("Writing/formatting tools added")

        if is_data:
            for t in cls.DATA_TOOLS:
                if t not in recommended:
                    recommended.append(t)
            rationale_parts.append("Data processing tools added")

        if is_file:
            for t in cls.FILE_TOOLS:
                if t not in recommended:
                    recommended.append(t)
            rationale_parts.append("File reading tools added")

        if is_coding:
            for t in cls.CODING_TOOLS:
                if t not in recommended:
                    recommended.append(t)
            rationale_parts.append("Coding/debug tools added")

        if is_browser:
            if "webpage_fetch" not in recommended:
                recommended.append("webpage_fetch")
            rationale_parts.append("Browser/web interaction tools added")

        # Always offer calculator for numeric tasks
        if re.search(r"\b(calculate|compute|math|sum|average|percent)\b", combined, re.I):
            if "calculator" not in recommended:
                recommended.append("calculator")

        # Filter to registered tools only
        registered = {t.name for t in tool_registry.list()}
        recommended = [t for t in recommended if t in registered]
        required = [t for t in required if t in registered]

        if not recommended and not is_research:
            # General fallback — expose safe research tools
            recommended = [t for t in cls.FACTUAL_TOOLS if t in registered]

        rationale = "; ".join(rationale_parts) if rationale_parts else "General tool availability"

        return ToolPlan(
            node_id=node_id,
            agent_name=agent_name,
            recommended_tools=recommended,
            required_tools=required,
            rationale=rationale,
            is_research_task=is_research,
        )

    @classmethod
    def plan_workflow_tools(
        cls,
        nodes: list[dict[str, Any]],
        objective: str = "",
    ) -> dict[str, ToolPlan]:
        """Build tool plans for all agent nodes in a workflow."""
        plans: dict[str, ToolPlan] = {}
        for node in nodes:
            if node.get("type") == "approval" or node.get("data", {}).get("nodeType") == "approval":
                continue
            plan = cls.infer_tools_for_node(node, objective)
            plans[plan.node_id] = plan
        return plans

    @classmethod
    def persist_plan(cls, ctx: ContextManager, plan: ToolPlan) -> None:
        """Store tool plan in node memory and workflow-level registry."""
        ctx.add_node_memory(plan.node_id, "planned_tools", plan.recommended_tools)
        ctx.add_node_memory(plan.node_id, "required_tools", plan.required_tools)
        ctx.add_node_memory(plan.node_id, "tool_plan", plan.to_dict())

        registry = ctx.get_workflow_memory("tool_plans") or {}
        registry[plan.node_id] = plan.to_dict()
        ctx.set_workflow_memory("tool_plans", registry)

    @classmethod
    def persist_workflow_plans(
        cls,
        ctx: ContextManager,
        plans: dict[str, ToolPlan],
    ) -> None:
        for plan in plans.values():
            cls.persist_plan(ctx, plan)

    @classmethod
    def build_tool_context_block(cls, plan: ToolPlan) -> str:
        """Generate prompt injection for recommended tools."""
        if not plan.recommended_tools:
            return ""

        lines = [
            "--- Planned Tool Usage ---",
            f"Task type: {'Research (factual tools required)' if plan.is_research_task else 'General execution'}",
            f"Rationale: {plan.rationale}",
            "",
            "Recommended tools for this task (use BEFORE guessing):",
        ]
        for tool_name in plan.recommended_tools:
            tool = tool_registry.get(tool_name)
            if tool:
                marker = " [REQUIRED]" if tool_name in plan.required_tools else ""
                lines.append(f"  - {tool_name}{marker}: {tool.description}")

        if plan.required_tools:
            lines.extend([
                "",
                "REQUIRED: You MUST invoke at least one required tool before final answer.",
                "For research: start with web_search, then webpage_fetch on relevant URLs.",
            ])

        lines.append("")
        return "\n".join(lines)

    @classmethod
    def inject_node_data(cls, node: dict[str, Any], plan: ToolPlan) -> dict[str, Any]:
        """Attach planned tools to node data for frontend observability."""
        data = dict(node.get("data", {}))
        data["plannedTools"] = plan.recommended_tools
        data["requiredTools"] = plan.required_tools
        data["toolPlan"] = plan.to_dict()
        return {**node, "data": data}
