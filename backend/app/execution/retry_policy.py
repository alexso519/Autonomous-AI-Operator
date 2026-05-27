"""
Smarter retry policy with reason classification and intentional prompt mutation.

Retries are typed, budgeted, and designed to produce meaningfully different outputs.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.context_manager import ContextManager
from app.execution.execution_quality import ExecutionQualityScore

logger = logging.getLogger(__name__)


class RetryType(str, Enum):
    CLARIFY = "clarify"
    SUMMARIZE = "summarize"
    EXPAND = "expand"
    FACTUAL_RETRY = "factual_retry"
    TOOL_RETRY = "tool_retry"


@dataclass
class RetryDecision:
    retry_type: RetryType
    reason: str
    strategy_label: str
    prompt_suffix: str
    goal_override: str | None
    avoid_patterns: list[str]
    budget_remaining: int


class RetryPolicy:
    """Classify failures, select strategies, and enforce global retry budget."""

    GLOBAL_RETRY_BUDGET = 5
    PER_NODE_MAX = 2

    RETRY_HINT_LABELS: dict[str, str] = {
        "clarify_goal_and_retry": "task scope and explicit assumptions",
        "expand_with_missing_sections": "Summary, Findings, Recommendations, and Next Steps",
        "summarize_and_deduplicate": "a concise deduplicated summary",
        "use_tools_for_facts": "tool-verified facts with [n] citations",
        "invoke_web_search_first": "web_search and webpage_fetch evidence",
        "add_actionable_recommendations": "actionable recommendations",
    }

    ISSUE_TO_RETRY: dict[str, RetryType] = {
        "empty_output": RetryType.CLARIFY,
        "explicit_failure": RetryType.CLARIFY,
        "incomplete_output": RetryType.EXPAND,
        "low_factuality": RetryType.FACTUAL_RETRY,
        "hallucination_risk": RetryType.FACTUAL_RETRY,
        "poor_tool_usage": RetryType.TOOL_RETRY,
        "repetitive_output": RetryType.SUMMARIZE,
        "low_usefulness": RetryType.EXPAND,
        "low_word_count": RetryType.EXPAND,
        "short_output": RetryType.EXPAND,
        "missing_section": RetryType.EXPAND,
        "stalled_output": RetryType.SUMMARIZE,
        "tool_execution_failure": RetryType.TOOL_RETRY,
        "unsupported_claims": RetryType.TOOL_RETRY,
    }

    @classmethod
    def get_global_retry_count(cls, ctx: ContextManager) -> int:
        return int(ctx.get_workflow_memory("global_retry_count") or 0)

    @classmethod
    def increment_global_retry(cls, ctx: ContextManager) -> int:
        count = cls.get_global_retry_count(ctx) + 1
        ctx.set_workflow_memory("global_retry_count", count)
        from app.execution.production_safety import ProductionSafetyGuard

        ProductionSafetyGuard.record_retry(ctx.execution_id)
        return count

    @classmethod
    def can_retry(cls, ctx: ContextManager, node_id: str) -> tuple[bool, str]:
        from app.execution.production_safety import ProductionSafetyGuard

        circuit = ProductionSafetyGuard.check_retry_circuit(ctx.execution_id)
        if not circuit.allowed:
            return False, circuit.reason

        global_count = cls.get_global_retry_count(ctx)
        if global_count >= cls.GLOBAL_RETRY_BUDGET:
            return False, "global_retry_budget_exhausted"

        node_counts = ctx.get_workflow_memory("reflection_retry_counts") or {}
        if int(node_counts.get(node_id, 0)) >= cls.PER_NODE_MAX:
            return False, "per_node_retry_limit_reached"

        return True, "ok"

    @classmethod
    def classify_failure(
        cls,
        quality: ExecutionQualityScore,
        legacy_issues: list[str] | None = None,
    ) -> RetryType:
        issues = list(quality.issues) + (legacy_issues or [])
        for issue in issues:
            if issue in cls.ISSUE_TO_RETRY:
                return cls.ISSUE_TO_RETRY[issue]

        if quality.hallucination_risk > 0.4 or quality.factuality_confidence < 0.5:
            return RetryType.FACTUAL_RETRY
        if quality.repetition_score > 0.4:
            return RetryType.SUMMARIZE
        if quality.completion_confidence < 0.5:
            return RetryType.EXPAND
        if quality.tool_usage_quality < 0.4:
            return RetryType.TOOL_RETRY
        return RetryType.CLARIFY

    @classmethod
    def select_strategy(
        cls,
        retry_type: RetryType,
        quality: ExecutionQualityScore,
        ctx: ContextManager,
        prior_output: str,
    ) -> RetryDecision:
        can, reason = cls.can_retry(ctx, quality.node_id)
        budget_remaining = cls.GLOBAL_RETRY_BUDGET - cls.get_global_retry_count(ctx)

        if not can:
            return RetryDecision(
                retry_type=retry_type,
                reason=reason,
                strategy_label="blocked",
                prompt_suffix="",
                goal_override=None,
                avoid_patterns=[],
                budget_remaining=0,
            )

        output_hash = hashlib.sha256(prior_output.encode()).hexdigest()[:12]
        prior_hashes = ctx.get_workflow_memory("retry_output_hashes") or []
        avoid_patterns = cls._extract_repeated_phrases(prior_output)

        suffix, goal_override, label = cls._build_strategy(
            retry_type, quality, prior_output, output_hash, prior_hashes
        )

        return RetryDecision(
            retry_type=retry_type,
            reason=", ".join(quality.issues) or quality.reasons[0] if quality.reasons else "low_quality",
            strategy_label=label,
            prompt_suffix=suffix,
            goal_override=goal_override,
            avoid_patterns=avoid_patterns,
            budget_remaining=budget_remaining,
        )

    @classmethod
    def record_retry_attempt(
        cls,
        ctx: ContextManager,
        node_id: str,
        decision: RetryDecision,
        output_hash: str | None = None,
    ) -> None:
        cls.increment_global_retry(ctx)
        history = ctx.get_workflow_memory("retry_history") or []
        history.append({
            "nodeId": node_id,
            "retryType": decision.retry_type.value,
            "reason": decision.reason,
            "strategy": decision.strategy_label,
            "budgetRemaining": decision.budget_remaining - 1,
        })
        ctx.set_workflow_memory("retry_history", history[-30:])

        if output_hash:
            hashes = ctx.get_workflow_memory("retry_output_hashes") or []
            hashes.append(output_hash)
            ctx.set_workflow_memory("retry_output_hashes", hashes[-20:])

    @classmethod
    def build_recovery_goal(
        cls,
        original_goal: str,
        agent_name: str,
        decision: RetryDecision,
        quality: ExecutionQualityScore,
        ctx: ContextManager | None = None,
    ) -> str:
        from app.execution.research_pipeline_state import is_research_pipeline_ready

        if (
            ctx is not None
            and is_research_pipeline_ready(ctx)
            and decision.retry_type in (RetryType.FACTUAL_RETRY, RetryType.TOOL_RETRY)
        ):
            return (
                f"Retry for {agent_name}. Pre-collected research evidence is already in context.\n"
                f"Original goal: {original_goal}\n\n"
                "Synthesize from the provided evidence. Write markdown prose with "
                "Summary, Findings, Recommendations, and Next Steps.\n"
                "Do NOT call web_search, webpage_fetch, or emit JSON tool requests.\n"
                f"{decision.prompt_suffix}"
            )

        if decision.goal_override:
            return decision.goal_override

        base = (
            f"Retry task for {agent_name}. Previous attempt scored "
            f"{quality.overall_score:.0%} quality.\n"
            f"Failure reason: {decision.reason}\n"
            f"Strategy: {decision.retry_type.value} — {decision.strategy_label}\n\n"
            f"Original goal: {original_goal}\n\n"
            f"{decision.prompt_suffix}"
        )
        if decision.avoid_patterns:
            base += (
                "\n\nDo NOT repeat these phrases or sections from the prior attempt:\n"
                + "\n".join(f"- {p[:120]}" for p in decision.avoid_patterns[:5])
            )
        return base

    @classmethod
    def _build_strategy(
        cls,
        retry_type: RetryType,
        quality: ExecutionQualityScore,
        prior_output: str,
        output_hash: str,
        prior_hashes: list[str],
    ) -> tuple[str, str | None, str]:
        duplicate_warning = ""
        if output_hash in prior_hashes:
            duplicate_warning = (
                "Your previous retry produced duplicate content. "
                "You MUST produce a substantially different response.\n"
            )

        if retry_type == RetryType.CLARIFY:
            return (
                duplicate_warning
                + "Clarify the task scope. State assumptions explicitly. "
                "Answer the core question directly in the first paragraph. "
                "Do not apologize or explain limitations — execute the task.",
                None,
                "clarify_and_focus",
            )

        if retry_type == RetryType.SUMMARIZE:
            return (
                duplicate_warning
                + "The prior output was repetitive. Produce a concise, deduplicated summary. "
                "Use bullet points. Remove restated ideas. Maximum 400 words unless more detail is essential.",
                "Summarize and deduplicate prior findings without repeating the same sentences.",
                "deduplicate_summary",
            )

        if retry_type == RetryType.EXPAND:
            missing = ", ".join(
                cls.RETRY_HINT_LABELS.get(h, h.replace("_", " "))
                for h in quality.retry_hints
            ) or "missing sections"
            return (
                duplicate_warning
                + f"Expand the prior output. Address: {missing}. "
                "Use clear headings: Summary, Findings, Recommendations, Next Steps. "
                "Be thorough but avoid filler.",
                None,
                "expand_missing_sections",
            )

        if retry_type == RetryType.FACTUAL_RETRY:
            return (
                duplicate_warning
                + "CRITICAL: Do not guess facts. Use web_search then webpage_fetch "
                "to verify claims before writing. Cite sources. "
                "Respond with tool JSON if you need external data.\n"
                '{"tool_name": "web_search", "input_data": {"query": "<your query>"}}',
                "Verify all factual claims using web_search and webpage_fetch. Cite sources.",
                "factual_verification",
            )

        if retry_type == RetryType.TOOL_RETRY:
            return (
                duplicate_warning
                + "You must use tools before answering. Start with web_search for research, "
                "then webpage_fetch on relevant URLs. Do not produce a text-only answer "
                "for factual research tasks.",
                "Use web_search and webpage_fetch tools before producing your answer.",
                "mandatory_tool_use",
            )

        return ("Improve the prior output.", None, "generic_retry")

    @classmethod
    def _extract_repeated_phrases(cls, text: str, min_len: int = 40) -> list[str]:
        import re

        phrases: list[str] = []
        for para in re.split(r"\n\n+", text.strip()):
            para = para.strip()
            if len(para) >= min_len:
                phrases.append(para[:200])
        return phrases[:5]
