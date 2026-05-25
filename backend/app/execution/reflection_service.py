"""
Deterministic reflection and rule-based replanning for workflow execution.

This module inspects intermediate agent outputs using stable heuristics,
records reflection findings into shared memory, and adapts the workflow
by injecting recovery or validation steps when execution quality is poor.

It is intentionally rule-based for MVP and avoids LLM-driven self-reflection.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.execution.context_manager import ContextManager
from app.planning.node_factory import create_edge

logger = logging.getLogger(__name__)


@dataclass
class ReflectionFinding:
    node_id: str
    agent_name: str
    output: str
    issues: list[str]
    should_replan: bool
    action: str
    recovery_type: str
    retry_count: int
    reason: str


class ReflectionService:
    """Rule-based evaluation of agent outputs and reflection state management."""

    MAX_RETRY_COUNT = 2
    MIN_OUTPUT_WORDS = 80
    LOW_QUALITY_WORDS = 50
    FAILURE_PATTERN = re.compile(
        r"\b(i can(?:'t|not)|cannot|can not|unable to|no output|nothing to report|error|failed|unable|not sure)\b",
        re.IGNORECASE,
    )
    STALLED_PATTERN = re.compile(
        r"\b(no new information|same as above|nothing changed|as before|repeat(ed)?|restate)\b",
        re.IGNORECASE,
    )
    HEDGE_PATTERN = re.compile(
        r"\b(might|could|possibly|perhaps|seems|suggests|may be|likely|uncertain|not convinced)\b",
        re.IGNORECASE,
    )
    MISSING_SECTION_KEYWORDS = [
        "summary",
        "recommendation",
        "next steps",
        "action items",
        "conclusion",
        "findings",
        "analysis",
        "plan",
        "roadmap",
    ]

    @classmethod
    def inspect_output(
        cls,
        node: dict[str, Any],
        ctx: ContextManager,
    ) -> ReflectionFinding:
        node_id = node.get("id", "unknown")
        agent_name = node.get("data", {}).get("label", "Agent")
        output = ctx.get_all_outputs().get(node_id, "").strip()
        output_lower = output.lower()
        words = re.findall(r"\w+", output)
        word_count = len(words)

        issues: list[str] = []

        if not output or output == "(no output)":
            issues.append("empty_output")

        if cls.FAILURE_PATTERN.search(output_lower):
            issues.append("explicit_failure")

        if cls.STALLED_PATTERN.search(output_lower):
            issues.append("stalled_output")

        if word_count < cls.LOW_QUALITY_WORDS:
            issues.append("low_word_count")
        elif word_count < cls.MIN_OUTPUT_WORDS:
            issues.append("short_output")

        if cls.HEDGE_PATTERN.search(output_lower):
            issues.append("low_confidence")

        if output_lower.endswith(("...", "…", "to be continued")):
            issues.append("incomplete_output")

        goal_text = node.get("data", {}).get("goal", "").lower()
        if cls._should_expect_structure(goal_text) and not cls._has_required_sections(output_lower, goal_text):
            issues.append("missing_section")

        retry_count = cls._get_retry_count(node_id, ctx)
        should_replan = bool(issues) and retry_count < cls.MAX_RETRY_COUNT
        action = "none"
        recovery_type = "none"
        reason = "No reflection issues detected."

        if issues:
            reason = ", ".join(sorted(set(issues)))
            if retry_count >= cls.MAX_RETRY_COUNT:
                reason = f"Reflection found issues but retry limit reached: {reason}"
                should_replan = False
                action = "max_retries_reached"
                recovery_type = "none"
            elif "explicit_failure" in issues or "execution_error" in issues:
                should_replan = True
                action = "retry_with_validation"
                recovery_type = "validation_then_retry"
            elif "missing_section" in issues or "incomplete_output" in issues:
                should_replan = True
                action = "validate_then_retry"
                recovery_type = "validation_then_retry"
            else:
                should_replan = True
                action = "retry"
                recovery_type = "retry"

        finding = ReflectionFinding(
            node_id=node_id,
            agent_name=agent_name,
            output=output,
            issues=issues,
            should_replan=should_replan,
            action=action,
            recovery_type=recovery_type,
            retry_count=retry_count,
            reason=reason,
        )

        cls._record_finding(finding, node, ctx)

        return finding

    @classmethod
    def record_tool_failure(
        cls,
        node_id: str,
        agent_name: str,
        error: str,
        ctx: ContextManager,
    ) -> None:
        issues = ["tool_execution_failure"]
        finding_data = {
            "issues": issues,
            "action": "retry_with_validation",
            "recoveryType": "validation_then_retry",
            "reason": error,
            "timestamp": uuid.uuid4().hex[:12],
        }
        ctx.add_node_memory(node_id, "reflection", finding_data)
        history = ctx.get_workflow_memory("reflection_history") or []
        history.append(
            {
                "nodeId": node_id,
                "agentName": agent_name,
                "issues": issues,
                "action": "tool_failure",
                "retryCount": 0,
                "timestamp": uuid.uuid4().hex[:12],
            }
        )
        ctx.set_workflow_memory("reflection_history", history)
        ctx.set_workflow_memory("last_tool_error", {"node_id": node_id, "error": error})

    @classmethod
    def _should_expect_structure(cls, goal_text: str) -> bool:
        return any(keyword in goal_text for keyword in [
            "plan",
            "strategy",
            "analysis",
            "report",
            "recommend",
            "summary",
            "review",
        ])

    @classmethod
    def _has_required_sections(cls, output_lower: str, goal_text: str) -> bool:
        required = []
        if any(word in goal_text for word in ["plan", "strategy", "roadmap", "proposal"]):
            required.extend(["plan", "roadmap", "next steps", "recommendation", "action items"])
        if any(word in goal_text for word in ["analysis", "research", "evaluate", "assessment"]):
            required.extend(["analysis", "findings", "conclusion", "insight", "key points"])
        if any(word in goal_text for word in ["summary", "recommend", "conclusion"]):
            required.extend(["summary", "recommendation", "conclusion", "takeaway"])
        if not required:
            required = cls.MISSING_SECTION_KEYWORDS
        return any(keyword in output_lower for keyword in required)

    @classmethod
    def _contains_hedges(cls, output_lower: str) -> bool:
        return bool(cls.HEDGE_PATTERN.search(output_lower))

    @classmethod
    def _get_retry_count(cls, node_id: str, ctx: ContextManager) -> int:
        counts = ctx.get_workflow_memory("reflection_retry_counts", {}) or {}
        return int(counts.get(node_id, 0))

    @classmethod
    def increment_retry_count(cls, node_id: str, ctx: ContextManager) -> None:
        counts = ctx.get_workflow_memory("reflection_retry_counts", {}) or {}
        counts[node_id] = int(counts.get(node_id, 0)) + 1
        ctx.set_workflow_memory("reflection_retry_counts", counts)

    @classmethod
    def _record_finding(cls, finding: ReflectionFinding, node: dict[str, Any], ctx: ContextManager) -> None:
        ctx.add_node_memory(
            finding.node_id,
            "reflection",
            {
                "issues": finding.issues,
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "retryCount": finding.retry_count,
                "reason": finding.reason,
            },
        )
        history = ctx.get_workflow_memory("reflection_history", []) or []
        history.append(
            {
                "nodeId": finding.node_id,
                "agentName": finding.agent_name,
                "issues": finding.issues,
                "action": finding.action,
                "retryCount": finding.retry_count,
                "timestamp": uuid.uuid4().hex[:12],
            }
        )
        ctx.set_workflow_memory("reflection_history", history)


class ReplanningService:
    """Adaptive insertion of recovery and validation workflow steps."""

    @classmethod
    def insert_recovery_steps(
        cls,
        sorted_nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_index: int,
        node: dict[str, Any],
        finding: ReflectionFinding,
        ctx: ContextManager,
    ) -> list[dict[str, Any]]:
        if not finding.should_replan:
            return sorted_nodes

        insertion_index = node_index + 1
        node_id = node.get("id", "unknown")
        position = node.get("position", {})
        base_x = position.get("x", 200)
        base_y = position.get("y", 0)
        injected_nodes: list[dict[str, Any]] = []

        # Add a validation node when the output shows explicit failure or missing sections.
        validator_node = None
        if finding.recovery_type == "validation_then_retry":
            validator_node = cls._build_recovery_node(
                source_node_id=node_id,
                role="Output Validator",
                label=f"Validate {finding.agent_name}",
                goal=(
                    "Review the previous output for correctness, completeness, and "
                    "missing information. Summarize issues and recommend improvements."
                ),
                position={"x": base_x, "y": base_y + 100},
                dependencies=[node_id],
                retry_count=finding.retry_count,
                suffix="validation",
            )
            injected_nodes.append(validator_node)
            edges.append(create_edge(node_id, validator_node["id"], len(edges)))

        # Add a retry node to improve or rerun the task with a clearer prompt.
        retry_node = cls._build_recovery_node(
            source_node_id=node_id,
            role="Retry Agent",
            label=f"Retry {finding.agent_name}",
            goal=(
                "Retry the previous task using the shared workflow context and any "
                "validation findings. Be more explicit, fill missing sections, and "
                "deliver a clear, actionable response."
            ),
            position={"x": base_x, "y": base_y + 180 if validator_node else base_y + 100},
            dependencies=[validator_node["id"]] if validator_node else [node_id],
            retry_count=finding.retry_count,
            suffix="retry",
        )
        injected_nodes.append(retry_node)
        if validator_node:
            edges.append(create_edge(validator_node["id"], retry_node["id"], len(edges)))
        else:
            edges.append(create_edge(node_id, retry_node["id"], len(edges)))

        # Insert new nodes immediately after the current node.
        sorted_nodes[insertion_index:insertion_index] = injected_nodes

        # Increment retry count so future reflection respects limits.
        ReflectionService.increment_retry_count(node_id, ctx)

        cls._record_replanning(node_id, finding, injected_nodes, ctx)

        logger.info(
            "Inserted recovery nodes for %s: %s",
            node_id,
            [n["id"] for n in injected_nodes],
        )

        return sorted_nodes

    @classmethod
    def _build_recovery_node(
        cls,
        source_node_id: str,
        role: str,
        label: str,
        goal: str,
        position: dict[str, int],
        dependencies: list[str],
        retry_count: int,
        suffix: str,
    ) -> dict[str, Any]:
        node_id = f"{source_node_id}-{suffix}-{retry_count + 1}"
        return {
            "id": node_id,
            "type": "agent",
            "data": {
                "label": label,
                "role": role,
                "goal": goal,
                "taskDescription": goal,
                "backstory": (
                    "You are a recovery specialist. Your role is to validate or retry "
                    "a prior agent output with higher quality and completeness."
                ),
                "dependencies": dependencies,
                "requiresApproval": False,
                "nodeType": "agent",
                "executionMetadata": {
                    "recovery": True,
                    "recoveryType": suffix,
                    "sourceNodeId": source_node_id,
                    "retryCount": retry_count + 1,
                    "createdBy": "ReflectionService",
                },
                "temperature": 0.3,
            },
            "position": position,
        }

    @classmethod
    def _record_replanning(
        cls,
        node_id: str,
        finding: ReflectionFinding,
        injected_nodes: list[dict[str, Any]],
        ctx: ContextManager,
    ) -> None:
        ctx.add_node_memory(
            node_id,
            "replanning",
            {
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "injectedNodes": [n["id"] for n in injected_nodes],
                "issues": finding.issues,
                "retryCount": finding.retry_count + 1,
            },
        )
        history = ctx.get_workflow_memory("replanning_history", []) or []
        history.append(
            {
                "nodeId": node_id,
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "injectedNodes": [n["id"] for n in injected_nodes],
                "issues": finding.issues,
                "timestamp": uuid.uuid4().hex[:12],
            }
        )
        ctx.set_workflow_memory("replanning_history", history)
