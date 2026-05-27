"""
Deterministic reflection and rule-based replanning for workflow execution.

Uses ExecutionQualityScorer for scoring and RetryPolicy for typed recovery.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.execution_quality import ExecutionQualityScorer
from app.execution.retry_policy import RetryDecision, RetryPolicy, RetryType
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
    quality_score: float = 0.0
    retry_type: str = "none"
    retry_decision: RetryDecision | None = None


class ReflectionService:
    """Quality-driven evaluation of agent outputs and reflection state management."""

    MAX_RETRY_COUNT = RetryPolicy.PER_NODE_MAX

    @classmethod
    def inspect_output(
        cls,
        node: dict[str, Any],
        ctx: ContextManager,
    ) -> ReflectionFinding:
        node_id = node.get("id", "unknown")
        agent_name = node.get("data", {}).get("label", "Agent")
        output = ctx.get_all_outputs().get(node_id, "").strip()

        quality = ExecutionQualityScorer.score_output(node, ctx)
        ExecutionQualityScorer.record_in_context(quality, ctx)

        # Prioritize factual recovery when unsupported claims detected
        if "unsupported_claims" in quality.issues or "hallucination_risk" in quality.issues:
            if quality.factuality_confidence < 0.6:
                quality.should_retry = True
                if "unsupported_claims" in quality.issues:
                    quality.retry_hints = ["use_tools_for_facts"] + quality.retry_hints

        retry_count = cls._get_retry_count(node_id, ctx)
        can_retry, budget_reason = RetryPolicy.can_retry(ctx, node_id)

        issues = list(quality.issues)
        retry_decision: RetryDecision | None = None
        should_replan = quality.should_retry and can_retry
        action = "none"
        recovery_type = "none"
        retry_type = "none"
        reason = (
            f"Quality score {quality.overall_score:.0%}: {', '.join(quality.reasons)}"
            if quality.reasons
            else "No reflection issues detected."
        )

        if quality.should_retry and not can_retry:
            should_replan = False
            action = "max_retries_reached"
            reason = f"Retry blocked: {budget_reason}. Issues: {', '.join(issues)}"
        elif should_replan:
            retry_type_enum = RetryPolicy.classify_failure(quality, issues)
            retry_type = retry_type_enum.value
            retry_decision = RetryPolicy.select_strategy(
                retry_type_enum, quality, ctx, output
            )
            action = f"retry_{retry_type}"
            recovery_type = retry_type
            reason = (
                f"Quality {quality.overall_score:.0%} — "
                f"{retry_decision.reason} → {retry_type}"
            )

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
            quality_score=quality.overall_score,
            retry_type=retry_type,
            retry_decision=retry_decision,
        )

        cls._record_finding(finding, node, ctx, quality)
        return finding

    @classmethod
    async def persist_quality_score(
        cls,
        execution_id: str,
        node: dict[str, Any],
        ctx: ContextManager,
    ) -> None:
        quality = ExecutionQualityScorer.score_output(node, ctx)
        await ExecutionQualityScorer.persist_score(execution_id, quality)

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
            "action": "retry_tool_retry",
            "recoveryType": RetryType.TOOL_RETRY.value,
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
    def _get_retry_count(cls, node_id: str, ctx: ContextManager) -> int:
        counts = ctx.get_workflow_memory("reflection_retry_counts", {}) or {}
        return int(counts.get(node_id, 0))

    @classmethod
    def increment_retry_count(cls, node_id: str, ctx: ContextManager) -> None:
        counts = ctx.get_workflow_memory("reflection_retry_counts", {}) or {}
        counts[node_id] = int(counts.get(node_id, 0)) + 1
        ctx.set_workflow_memory("reflection_retry_counts", counts)

    @classmethod
    def _record_finding(
        cls,
        finding: ReflectionFinding,
        node: dict[str, Any],
        ctx: ContextManager,
        quality: Any,
    ) -> None:
        ctx.add_node_memory(
            finding.node_id,
            "reflection",
            {
                "issues": finding.issues,
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "retryCount": finding.retry_count,
                "reason": finding.reason,
                "qualityScore": finding.quality_score,
                "retryType": finding.retry_type,
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
                "qualityScore": finding.quality_score,
                "retryType": finding.retry_type,
                "timestamp": uuid.uuid4().hex[:12],
            }
        )
        ctx.set_workflow_memory("reflection_history", history)


class ReplanningService:
    """Adaptive insertion of typed recovery workflow steps."""

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
        if not finding.should_replan or finding.retry_decision is None:
            return sorted_nodes

        insertion_index = node_index + 1
        node_id = node.get("id", "unknown")
        position = node.get("position", {})
        base_x = position.get("x", 200)
        base_y = position.get("y", 0)

        quality = ExecutionQualityScorer.score_output(node, ctx)
        decision = finding.retry_decision
        original_goal = node.get("data", {}).get("goal", "Complete the task.")
        recovery_goal = RetryPolicy.build_recovery_goal(
            original_goal,
            finding.agent_name,
            decision,
            quality,
            ctx=ctx,
        )

        output_hash = hashlib.sha256(finding.output.encode()).hexdigest()[:12]
        RetryPolicy.record_retry_attempt(ctx, node_id, decision, output_hash)

        retry_node = cls._build_recovery_node(
            source_node_id=node_id,
            role=f"{decision.retry_type.value.title()} Recovery Agent",
            label=f"Retry ({decision.retry_type.value}) {finding.agent_name}",
            goal=recovery_goal,
            position={"x": base_x, "y": base_y + 120},
            dependencies=[node_id],
            retry_count=finding.retry_count,
            suffix=decision.retry_type.value,
            retry_type=decision.retry_type.value,
            strategy=decision.strategy_label,
        )
        injected_nodes = [retry_node]
        edges.append(create_edge(node_id, retry_node["id"], len(edges)))

        sorted_nodes[insertion_index:insertion_index] = injected_nodes
        ReflectionService.increment_retry_count(node_id, ctx)
        cls._record_replanning(node_id, finding, injected_nodes, ctx, decision)

        logger.info(
            "Inserted %s recovery node for %s: %s",
            decision.retry_type.value,
            node_id,
            retry_node["id"],
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
        retry_type: str,
        strategy: str,
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
                    "You are a recovery specialist executing a typed retry strategy. "
                    "Follow the retry instructions precisely and produce a meaningfully "
                    "different, higher-quality response."
                ),
                "dependencies": dependencies,
                "requiresApproval": False,
                "nodeType": "agent",
                "executionMetadata": {
                    "recovery": True,
                    "recoveryType": suffix,
                    "retryType": retry_type,
                    "retryStrategy": strategy,
                    "sourceNodeId": source_node_id,
                    "retryCount": retry_count + 1,
                    "createdBy": "RetryPolicy",
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
        decision: RetryDecision,
    ) -> None:
        ctx.add_node_memory(
            node_id,
            "replanning",
            {
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "retryType": decision.retry_type.value,
                "strategy": decision.strategy_label,
                "injectedNodes": [n["id"] for n in injected_nodes],
                "issues": finding.issues,
                "qualityScore": finding.quality_score,
                "retryCount": finding.retry_count + 1,
            },
        )
        history = ctx.get_workflow_memory("replanning_history", []) or []
        history.append(
            {
                "nodeId": node_id,
                "action": finding.action,
                "recoveryType": finding.recovery_type,
                "retryType": decision.retry_type.value,
                "injectedNodes": [n["id"] for n in injected_nodes],
                "issues": finding.issues,
                "qualityScore": finding.quality_score,
                "timestamp": uuid.uuid4().hex[:12],
            }
        )
        ctx.set_workflow_memory("replanning_history", history)
