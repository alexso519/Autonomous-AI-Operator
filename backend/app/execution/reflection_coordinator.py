"""
Centralized reflection coordination layer.

Consolidates reflection decisions, recovery-node suppression,
retry gating, and hedge-word filtering for local LLM stability.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Awaitable

from app.execution.context_manager import ContextManager
from app.execution.contracts import (
    QualityResultContract,
    ReflectionOutcome,
    RetryIntent,
    RetryRequest,
)
from app.execution.execution_quality import ExecutionQualityScorer
from app.execution.reflection_service import (
    ReflectionFinding,
    ReflectionService,
    ReplanningService,
)
from app.execution.retry_coordinator import RetryCoordinator
from app.execution.runtime_lifecycle import ExecutionPhase, LifecycleManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

# Issues that alone justify a retry (hedge-only is NOT in this set)
_CRITICAL_RETRY_ISSUES = frozenset({
    "empty_output",
    "explicit_failure",
    "incomplete_output",
    "hallucination_risk",
    "poor_tool_usage",
    "repetitive_output",
    "unsupported_claims",
    "tool_execution_failure",
    "low_usefulness",
})


class ReflectionCoordinator:
    """
    Centralized reflection decisions with deterministic reasoning
    and recovery-node suppression.
    """

    def __init__(
        self,
        execution_id: str,
        ctx: ContextManager,
        lifecycle: LifecycleManager | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.ctx = ctx
        self.lifecycle = lifecycle or LifecycleManager(execution_id)
        self.retry_coordinator = RetryCoordinator(execution_id, ctx)

    @classmethod
    def is_recovery_node(cls, node: dict[str, Any]) -> bool:
        return bool(node.get("data", {}).get("executionMetadata", {}).get("recovery"))

    @classmethod
    def is_hedge_only_retry(cls, quality: QualityResultContract) -> bool:
        """
        Local LLM hedge wording alone must NOT trigger retries.

        Suppresses retry when no critical quality issues are present,
        even if hedging language lowered the overall score.
        """
        critical = set(quality.issues) & _CRITICAL_RETRY_ISSUES
        if critical:
            return False

        if quality.score is None:
            return False

        reasons = set(quality.score.reasons)
        fact_dim = quality.score.dimensions.get("factuality")
        factuality_reasons = set(fact_dim.reasons) if fact_dim else set()

        has_hedge = (
            "hedging_language" in reasons or "hedging_language" in factuality_reasons
        )
        if not has_hedge:
            return False

        # No critical issues and hedging present → do not retry
        return True

    def evaluate(self, node: dict[str, Any]) -> ReflectionOutcome:
        """
        Evaluate node output quality and decide on reflection/replanning.

        Recovery nodes are suppressed to prevent retry-chain explosions.
        """
        node_id = node.get("id", "unknown")
        agent_name = node.get("data", {}).get("label", "Agent")
        reasoning: list[str] = []

        if self.is_recovery_node(node):
            reasoning.append("recovery_node_suppressed")
            return ReflectionOutcome(
                node_id=node_id,
                agent_name=agent_name,
                should_replan=False,
                action="suppressed_recovery_node",
                recovery_type="none",
                retry_type="none",
                reason="Recovery nodes are excluded from re-evaluation",
                reasoning_trail=reasoning,
                quality_score=0.0,
                issues=[],
                retry_count=0,
                suppressed=True,
                suppression_reason="recovery_node",
            )

        quality_score = ExecutionQualityScorer.score_output(node, self.ctx)
        ExecutionQualityScorer.record_in_context(quality_score, self.ctx)
        quality = QualityResultContract.from_score(quality_score)

        reasoning.append(f"quality_score={quality.overall_score:.2f}")
        reasoning.append(f"issues={quality.issues}")

        if self.is_hedge_only_retry(quality):
            reasoning.append("hedge_only_suppressed")
            finding = ReflectionFinding(
                node_id=node_id,
                agent_name=agent_name,
                output=self.ctx.get_all_outputs().get(node_id, ""),
                issues=list(quality.issues),
                should_replan=False,
                action="hedge_suppressed",
                recovery_type="none",
                retry_count=ReflectionService._get_retry_count(node_id, self.ctx),
                reason=(
                    f"Quality {quality.overall_score:.0%} — hedge wording alone "
                    "does not trigger retry on local models"
                ),
                quality_score=quality.overall_score,
                retry_type="none",
            )
            ReflectionService._record_finding(finding, node, self.ctx, quality_score)
            return ReflectionOutcome(
                node_id=node_id,
                agent_name=agent_name,
                should_replan=False,
                action="hedge_suppressed",
                recovery_type="none",
                retry_type="none",
                reason=finding.reason,
                reasoning_trail=reasoning,
                quality_score=quality.overall_score,
                issues=list(quality.issues),
                retry_count=finding.retry_count,
                suppressed=True,
                suppression_reason="hedge_only",
            )

        # Route through unified retry coordinator
        output = self.ctx.get_all_outputs().get(node_id, "").strip()
        retry_count = ReflectionService._get_retry_count(node_id, self.ctx)

        if quality.should_retry:
            request = RetryRequest(
                execution_id=self.execution_id,
                node_id=node_id,
                agent_name=agent_name,
                intent=RetryIntent.REFLECTION,
                reason=", ".join(quality.issues) or "low_quality",
                quality=quality_score,
                prior_output=output,
                output_hash=hashlib.sha256(output.encode()).hexdigest()[:12],
            )
            coord_result = self.retry_coordinator.evaluate(request)
            reasoning.append(f"retry_allowed={coord_result.allowed}")
            if coord_result.block_reason:
                reasoning.append(f"block_reason={coord_result.block_reason}")

            if coord_result.allowed and coord_result.decision:
                retry_type = coord_result.retry_type.value if coord_result.retry_type else "none"
                action = f"retry_{retry_type}"
                reason = (
                    f"Quality {quality.overall_score:.0%} — "
                    f"{coord_result.decision.reason} → {retry_type}"
                )
                reasoning.append(f"action={action}")
                return ReflectionOutcome(
                    node_id=node_id,
                    agent_name=agent_name,
                    should_replan=True,
                    action=action,
                    recovery_type=retry_type,
                    retry_type=retry_type,
                    reason=reason,
                    reasoning_trail=reasoning,
                    quality_score=quality.overall_score,
                    issues=list(quality.issues),
                    retry_count=retry_count,
                    retry_decision=coord_result.decision,
                )

            if not coord_result.allowed:
                action = "max_retries_reached" if "budget" in coord_result.block_reason or "limit" in coord_result.block_reason else "retry_blocked"
                return ReflectionOutcome(
                    node_id=node_id,
                    agent_name=agent_name,
                    should_replan=False,
                    action=action,
                    recovery_type="none",
                    retry_type="none",
                    reason=f"Retry blocked: {coord_result.block_reason}. Issues: {', '.join(quality.issues)}",
                    reasoning_trail=reasoning,
                    quality_score=quality.overall_score,
                    issues=list(quality.issues),
                    retry_count=retry_count,
                )

        reasoning.append("no_retry_needed")
        retry_count = ReflectionService._get_retry_count(node_id, self.ctx)
        reason = (
            f"Quality score {quality.overall_score:.0%}: acceptable"
            if quality.score and quality.score.reasons
            else "No reflection issues detected."
        )
        finding = ReflectionFinding(
            node_id=node_id,
            agent_name=agent_name,
            output=output,
            issues=list(quality.issues),
            should_replan=False,
            action="none",
            recovery_type="none",
            retry_count=retry_count,
            reason=reason,
            quality_score=quality.overall_score,
            retry_type="none",
        )
        ReflectionService._record_finding(finding, node, self.ctx, quality_score)
        return ReflectionOutcome(
            node_id=node_id,
            agent_name=agent_name,
            should_replan=False,
            action="none",
            recovery_type="none",
            retry_type="none",
            reason=reason,
            reasoning_trail=reasoning,
            quality_score=quality.overall_score,
            issues=list(quality.issues),
            retry_count=retry_count,
        )

    async def persist_quality(self, node: dict[str, Any]) -> None:
        await ReflectionService.persist_quality_score(
            self.execution_id, node, self.ctx
        )

    def insert_recovery_steps(
        self,
        sorted_nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_index: int,
        node: dict[str, Any],
        outcome: ReflectionOutcome,
    ) -> list[dict[str, Any]]:
        """Insert recovery nodes using ReplanningService with coordinator tracking."""
        if not outcome.should_replan or outcome.retry_decision is None:
            return sorted_nodes

        finding = ReflectionFinding(
            node_id=outcome.node_id,
            agent_name=outcome.agent_name,
            output=self.ctx.get_all_outputs().get(outcome.node_id, ""),
            issues=outcome.issues,
            should_replan=True,
            action=outcome.action,
            recovery_type=outcome.recovery_type,
            retry_count=outcome.retry_count,
            reason=outcome.reason,
            quality_score=outcome.quality_score,
            retry_type=outcome.retry_type,
            retry_decision=outcome.retry_decision,
        )

        if self.lifecycle:
            self.lifecycle.set_phase(ExecutionPhase.REPLANNING)

        return ReplanningService.insert_recovery_steps(
            sorted_nodes=sorted_nodes,
            edges=edges,
            node_index=node_index,
            node=node,
            finding=finding,
            ctx=self.ctx,
        )

    async def evaluate_and_emit(
        self,
        node: dict[str, Any],
        emit_fn: EmitFn,
        graph: Any = None,
        idx: int = 0,
        sorted_nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]] | None:
        """
        Full reflection pipeline: evaluate, emit events, optionally replan.

        Returns updated sorted_nodes if replanning occurred, else None.
        """
        from app.execution.runtime_performance import RuntimePerformanceTracker

        if sorted_nodes is None:
            sorted_nodes = []

        agent_name = node.get("data", {}).get("label", "Agent")
        RuntimePerformanceTracker.begin_stage(
            self.execution_id, "reflection", agent_name
        )

        if self.lifecycle:
            self.lifecycle.set_phase(ExecutionPhase.REFLECTING)

        outcome = self.evaluate(node)
        await self.persist_quality(node)

        causal_context: dict[str, Any] = {}
        try:
            from app.intelligence.knowledge_reasoning_coordinator import (
                KnowledgeReasoningCoordinator,
            )

            objective = str(self.ctx.get_workflow_memory("objective") or "")
            causal_context = await KnowledgeReasoningCoordinator.query_causal_for_reflection(
                objective[:80],
            )
            if causal_context.get("causalLinks"):
                self.ctx.set_workflow_memory("reflection_causal_context", causal_context)
        except Exception:
            pass

        await emit_fn(
            self.execution_id,
            "quality_scored",
            outcome.agent_name,
            f"Quality score: {outcome.quality_score:.0%} — {outcome.reason}",
            nodeId=outcome.node_id,
            qualityScore=outcome.quality_score,
            issues=outcome.issues,
            retryType=outcome.retry_type,
            shouldReplan=outcome.should_replan,
            reasoningTrail=outcome.reasoning_trail,
            suppressed=outcome.suppressed,
        )

        if not outcome.should_replan:
            RuntimePerformanceTracker.end_stage(
                self.execution_id, "reflection", agent_name
            )
            if outcome.action in ("max_retries_reached", "retry_blocked"):
                await emit_fn(
                    self.execution_id,
                    "reflection_limit_reached",
                    "system",
                    f"Reflection limit reached for {outcome.agent_name}: {outcome.reason}",
                    nodeId=outcome.node_id,
                    issues=outcome.issues,
                    qualityScore=outcome.quality_score,
                )
            return sorted_nodes

        if graph is not None and edges is not None:
            from app.execution.dynamic_planner import DynamicPlanner

            sorted_nodes = await DynamicPlanner.replan_remaining(
                execution_id=self.execution_id,
                graph=graph,
                node=node,
                finding=ReflectionFinding(
                    node_id=outcome.node_id,
                    agent_name=outcome.agent_name,
                    output=self.ctx.get_all_outputs().get(outcome.node_id, ""),
                    issues=outcome.issues,
                    should_replan=True,
                    action=outcome.action,
                    recovery_type=outcome.recovery_type,
                    retry_count=outcome.retry_count,
                    reason=outcome.reason,
                    quality_score=outcome.quality_score,
                    retry_type=outcome.retry_type,
                    retry_decision=outcome.retry_decision,
                ),
                sorted_nodes=sorted_nodes,
                edges=edges,
                node_index=idx - 1,
                ctx=self.ctx,
                emit_fn=emit_fn,
            )
        elif edges is not None:
            sorted_nodes = self.insert_recovery_steps(
                sorted_nodes=sorted_nodes,
                edges=edges,
                node_index=idx - 1,
                node=node,
                outcome=outcome,
            )

        await self.ctx.persist()

        message = (
            f"Quality {outcome.quality_score:.0%} for {outcome.agent_name}. "
            f"Injected {outcome.retry_type} recovery step."
        )
        await emit_fn(
            self.execution_id,
            "reflection_action",
            "system",
            message,
            nodeId=outcome.node_id,
            issues=outcome.issues,
            action=outcome.action,
            retryType=outcome.retry_type,
            qualityScore=outcome.quality_score,
            retryCount=outcome.retry_count + 1,
            retryScheduled=True,
            reason=outcome.reason,
            reasoningTrail=outcome.reasoning_trail,
        )

        if logs is not None:
            import uuid
            from datetime import datetime, timezone

            logs.append({
                "id": uuid.uuid4().hex[:12],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "warning",
                "agentName": "system",
                "content": message,
            })

        RuntimePerformanceTracker.end_stage(
            self.execution_id, "reflection", agent_name
        )
        return sorted_nodes
