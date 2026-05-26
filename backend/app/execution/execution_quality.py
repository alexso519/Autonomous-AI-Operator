"""
Deterministic execution quality scoring for agent outputs.

Provides numeric, inspectable scores used by reflection and retry policy
instead of simple keyword heuristics alone.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.hallucination_guard import HallucinationGuard

logger = logging.getLogger(__name__)


@dataclass
class QualityDimension:
    name: str
    score: float
    weight: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExecutionQualityScore:
    node_id: str
    agent_name: str
    overall_score: float
    factuality_confidence: float
    completion_confidence: float
    repetition_score: float
    hallucination_risk: float
    tool_usage_quality: float
    output_usefulness: float
    dimensions: dict[str, QualityDimension]
    reasons: list[str]
    issues: list[str]
    should_retry: bool
    retry_hints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "agentName": self.agent_name,
            "overallScore": round(self.overall_score, 3),
            "factualityConfidence": round(self.factuality_confidence, 3),
            "completionConfidence": round(self.completion_confidence, 3),
            "repetitionScore": round(self.repetition_score, 3),
            "hallucinationRisk": round(self.hallucination_risk, 3),
            "toolUsageQuality": round(self.tool_usage_quality, 3),
            "outputUsefulness": round(self.output_usefulness, 3),
            "dimensions": {
                k: {
                    "score": round(v.score, 3),
                    "weight": v.weight,
                    "reasons": v.reasons,
                }
                for k, v in self.dimensions.items()
            },
            "reasons": self.reasons,
            "issues": self.issues,
            "shouldRetry": self.should_retry,
            "retryHints": self.retry_hints,
        }


class ExecutionQualityScorer:
    """Score agent outputs using deterministic heuristics."""

    MIN_OUTPUT_WORDS = 80
    LOW_QUALITY_WORDS = 50

    HEDGE_PATTERN = re.compile(
        r"\b(might|could|possibly|perhaps|seems|suggests|may be|likely|uncertain|not sure|i think|probably)\b",
        re.IGNORECASE,
    )
    FAILURE_PATTERN = re.compile(
        r"\b(i can(?:'t|not)|cannot|can not|unable to|no output|nothing to report|error|failed|unable)\b",
        re.IGNORECASE,
    )
    STALLED_PATTERN = re.compile(
        r"\b(no new information|same as above|nothing changed|as before|repeat(ed)?|restate)\b",
        re.IGNORECASE,
    )
    HALLUCINATION_PATTERN = re.compile(
        r"\b(as an ai|i don't have access|i cannot browse|made up|fabricated|placeholder|lorem ipsum|example\.com/fake)\b",
        re.IGNORECASE,
    )
    UNCITED_CLAIM_PATTERN = re.compile(
        r"\b(according to|research shows|studies indicate|data suggests|reports say)\b",
        re.IGNORECASE,
    )
    RESEARCH_GOAL_PATTERN = re.compile(
        r"\b(research|investigate|find|lookup|search|current|latest|news|compare|analyze external)\b",
        re.IGNORECASE,
    )
    STRUCTURE_KEYWORDS = [
        "summary", "recommendation", "next steps", "action items",
        "conclusion", "findings", "analysis", "plan", "roadmap",
    ]

    @classmethod
    def score_output(
        cls,
        node: dict[str, Any],
        ctx: ContextManager,
    ) -> ExecutionQualityScore:
        node_id = node.get("id", "unknown")
        node_data = node.get("data", {})
        agent_name = node_data.get("label", "Agent")
        goal = node_data.get("goal", "")
        output = ctx.get_all_outputs().get(node_id, "").strip()
        if not output:
            stored = ctx.get_node_memory(node_id).get("output", "")
            output = stored.strip() if stored else ""

        words = re.findall(r"\w+", output)
        word_count = len(words)
        output_lower = output.lower()
        goal_lower = goal.lower()

        prior_outputs = cls._prior_outputs(node_id, ctx)
        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        planned_tools = (
            ctx.get_node_memory(node_id).get("planned_tools")
            or node_data.get("plannedTools")
            or []
        )
        is_research_task = bool(cls.RESEARCH_GOAL_PATTERN.search(goal_lower))

        # ── Factuality confidence ─────────────────────────────────
        factuality_reasons: list[str] = []
        factuality = 0.85
        if not output or output == "(no output)":
            factuality = 0.0
            factuality_reasons.append("empty_output")
        else:
            if cls.HEDGE_PATTERN.search(output_lower):
                factuality -= 0.15
                factuality_reasons.append("hedging_language")
            if cls.UNCITED_CLAIM_PATTERN.search(output_lower) and not tool_calls:
                factuality -= 0.25
                factuality_reasons.append("uncited_factual_claims")
            if is_research_task:
                node_tools = [
                    t for t in tool_calls
                    if t.get("node_id") == node_id and t.get("status") == "completed"
                ]
                if not node_tools:
                    factuality -= 0.35
                    factuality_reasons.append("research_without_tool_evidence")
                else:
                    factuality += 0.1
                    factuality_reasons.append("tool_evidence_present")
            if "http://" in output_lower or "https://" in output_lower:
                factuality += 0.05
                factuality_reasons.append("contains_references")
        factuality = max(0.0, min(1.0, factuality))

        # ── Completion confidence ─────────────────────────────────
        completion_reasons: list[str] = []
        completion = 0.9
        if word_count < cls.LOW_QUALITY_WORDS:
            completion = 0.2
            completion_reasons.append("very_short_output")
        elif word_count < cls.MIN_OUTPUT_WORDS:
            completion = 0.45
            completion_reasons.append("below_minimum_length")
        if output_lower.endswith(("...", "…", "to be continued")):
            completion -= 0.3
            completion_reasons.append("incomplete_ending")
        if cls._expects_structure(goal_lower) and not cls._has_structure(output_lower):
            completion -= 0.25
            completion_reasons.append("missing_expected_sections")
        if cls.FAILURE_PATTERN.search(output_lower):
            completion = min(completion, 0.15)
            completion_reasons.append("explicit_failure_language")
        completion = max(0.0, min(1.0, completion))

        # ── Repetition detection (higher = more repetitive = worse) ─
        repetition_reasons: list[str] = []
        repetition_penalty = 0.0
        if cls.STALLED_PATTERN.search(output_lower):
            repetition_penalty += 0.5
            repetition_reasons.append("stalled_language")
        overlap = cls._max_overlap_ratio(output, prior_outputs)
        if overlap > 0.6:
            repetition_penalty += 0.4
            repetition_reasons.append(f"high_overlap_with_prior({overlap:.2f})")
        elif overlap > 0.35:
            repetition_penalty += 0.2
            repetition_reasons.append(f"moderate_overlap({overlap:.2f})")
        repetition_score = max(0.0, min(1.0, repetition_penalty))

        # ── Hallucination heuristics (higher = worse) ────────────
        hallucination_reasons: list[str] = []
        hallucination = 0.05
        if cls.HALLUCINATION_PATTERN.search(output_lower):
            hallucination += 0.45
            hallucination_reasons.append("ai_limitation_phrasing")
        if is_research_task and not tool_calls and word_count > 100:
            hallucination += 0.35
            hallucination_reasons.append("long_research_answer_without_tools")
        fake_url = re.search(r"https?://[^\s]*(?:example|fake|placeholder)", output_lower)
        if fake_url:
            hallucination += 0.4
            hallucination_reasons.append("suspicious_url")

        # Hallucination guard layer — grounded verification
        guard = HallucinationGuard.assess(
            output,
            goal=goal,
            tool_calls=[t for t in tool_calls if t.get("node_id") == node_id],
            is_research=is_research_task,
        )
        if guard.hallucination_risk > hallucination:
            hallucination = guard.hallucination_risk
        hallucination_reasons.extend(guard.indicators)
        factuality = HallucinationGuard.apply_factuality_penalty(factuality, guard)
        guard_needs_tool_retry = guard.should_tool_retry
        hallucination = max(0.0, min(1.0, hallucination))

        # ── Tool usage quality ────────────────────────────────────
        tool_reasons: list[str] = []
        tool_quality = 0.7
        node_tool_calls = [t for t in tool_calls if t.get("node_id") == node_id]
        if planned_tools:
            used = {t.get("tool_name") for t in node_tool_calls if t.get("status") == "completed"}
            planned_set = set(planned_tools)
            if used & planned_set:
                tool_quality = 0.95
                tool_reasons.append("used_planned_tools")
            elif is_research_task:
                tool_quality = 0.25
                tool_reasons.append("ignored_planned_research_tools")
        elif is_research_task:
            if node_tool_calls:
                tool_quality = 0.85
                tool_reasons.append("research_tools_used")
            else:
                tool_quality = 0.2
                tool_reasons.append("research_task_no_tools")
        elif node_tool_calls:
            tool_quality = 0.9
            tool_reasons.append("tools_used_appropriately")
        tool_quality = max(0.0, min(1.0, tool_quality))

        # ── Output usefulness ─────────────────────────────────────
        usefulness_reasons: list[str] = []
        usefulness = 0.5
        if re.search(r"^[-*]\s+", output, re.MULTILINE):
            usefulness += 0.2
            usefulness_reasons.append("has_actionable_bullets")
        if re.search(r"^#{1,3}\s+", output, re.MULTILINE):
            usefulness += 0.15
            usefulness_reasons.append("structured_headings")
        if any(kw in output_lower for kw in ["recommend", "next step", "action", "should"]):
            usefulness += 0.15
            usefulness_reasons.append("action_oriented")
        if word_count >= cls.MIN_OUTPUT_WORDS:
            usefulness += 0.1
        if repetition_score > 0.5:
            usefulness -= 0.2
            usefulness_reasons.append("repetitive_content")
        usefulness = max(0.0, min(1.0, usefulness))

        dimensions = {
            "factuality": QualityDimension("factuality", factuality, 0.25, factuality_reasons),
            "completion": QualityDimension("completion", completion, 0.20, completion_reasons),
            "repetition": QualityDimension("repetition", 1.0 - repetition_score, 0.15, repetition_reasons),
            "hallucination": QualityDimension("hallucination", 1.0 - hallucination, 0.15, hallucination_reasons),
            "tool_usage": QualityDimension("tool_usage", tool_quality, 0.15, tool_reasons),
            "usefulness": QualityDimension("usefulness", usefulness, 0.10, usefulness_reasons),
        }

        overall = sum(d.score * d.weight for d in dimensions.values())

        issues: list[str] = []
        if factuality < 0.5:
            issues.append("low_factuality")
        if completion < 0.5:
            issues.append("incomplete_output")
        if repetition_score > 0.4:
            issues.append("repetitive_output")
        if hallucination > 0.4:
            issues.append("hallucination_risk")
        if tool_quality < 0.4:
            issues.append("poor_tool_usage")
        if guard_needs_tool_retry:
            issues.append("unsupported_claims")
        if usefulness < 0.4:
            issues.append("low_usefulness")
        if cls.FAILURE_PATTERN.search(output_lower):
            issues.append("explicit_failure")
        if not output or output == "(no output)":
            issues.append("empty_output")

        retry_hints = cls._build_retry_hints(issues, is_research_task, word_count)
        should_retry = overall < 0.55 or bool(
            set(issues) & {
                "empty_output", "explicit_failure", "incomplete_output",
                "hallucination_risk", "poor_tool_usage", "repetitive_output",
                "unsupported_claims",
            }
        )

        all_reasons = sorted(
            {r for d in dimensions.values() for r in d.reasons}
        )

        return ExecutionQualityScore(
            node_id=node_id,
            agent_name=agent_name,
            overall_score=overall,
            factuality_confidence=factuality,
            completion_confidence=completion,
            repetition_score=repetition_score,
            hallucination_risk=hallucination,
            tool_usage_quality=tool_quality,
            output_usefulness=usefulness,
            dimensions=dimensions,
            reasons=all_reasons,
            issues=issues,
            should_retry=should_retry,
            retry_hints=retry_hints,
        )

    @classmethod
    async def persist_score(
        cls,
        execution_id: str,
        score: ExecutionQualityScore,
    ) -> None:
        """Persist quality score to SQLite."""
        try:
            from app.database.database import get_db

            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            score_id = uuid.uuid4().hex[:16]
            await db.execute(
                """INSERT INTO execution_quality_scores
                   (id, execution_id, node_id, agent_name, overall_score,
                    factuality_confidence, completion_confidence, repetition_score,
                    hallucination_risk, tool_usage_quality, output_usefulness,
                    reasons, issues, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    score_id,
                    execution_id,
                    score.node_id,
                    score.agent_name,
                    score.overall_score,
                    score.factuality_confidence,
                    score.completion_confidence,
                    score.repetition_score,
                    score.hallucination_risk,
                    score.tool_usage_quality,
                    score.output_usefulness,
                    json.dumps(score.reasons),
                    json.dumps(score.issues),
                    now,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Failed to persist quality score: %s", exc)

    @classmethod
    def record_in_context(cls, score: ExecutionQualityScore, ctx: ContextManager) -> None:
        """Store latest score in shared memory for reflection/retry."""
        ctx.add_node_memory(score.node_id, "quality_score", score.to_dict())
        history = ctx.get_workflow_memory("quality_score_history") or []
        history.append({**score.to_dict(), "timestamp": datetime.now(timezone.utc).isoformat()})
        ctx.set_workflow_memory("quality_score_history", history[-50:])
        ctx.set_workflow_memory("last_quality_score", score.to_dict())

    @classmethod
    def _prior_outputs(cls, node_id: str, ctx: ContextManager) -> list[str]:
        outputs: list[str] = []
        for nid, data in ctx.get_all_outputs().items():
            if nid == node_id:
                continue
            text = data if isinstance(data, str) else str(data)
            if text.strip():
                outputs.append(text.strip())
        return outputs

    @classmethod
    def _max_overlap_ratio(cls, text: str, prior: list[str]) -> float:
        if not prior or not text.strip():
            return 0.0
        words_a = set(re.findall(r"\w+", text.lower()))
        if len(words_a) < 10:
            return 0.0
        max_ratio = 0.0
        for prev in prior:
            words_b = set(re.findall(r"\w+", prev.lower()))
            if not words_b:
                continue
            overlap = len(words_a & words_b) / max(len(words_a), 1)
            max_ratio = max(max_ratio, overlap)
        return max_ratio

    @classmethod
    def _expects_structure(cls, goal_lower: str) -> bool:
        return any(kw in goal_lower for kw in [
            "plan", "strategy", "analysis", "report", "recommend", "summary", "review",
        ])

    @classmethod
    def _has_structure(cls, output_lower: str) -> bool:
        if re.search(r"^#{1,3}\s+", output_lower, re.MULTILINE):
            return True
        return any(kw in output_lower for kw in cls.STRUCTURE_KEYWORDS)

    @classmethod
    def _build_retry_hints(
        cls,
        issues: list[str],
        is_research: bool,
        word_count: int,
    ) -> list[str]:
        hints: list[str] = []
        if "empty_output" in issues or "explicit_failure" in issues:
            hints.append("clarify_goal_and_retry")
        if "incomplete_output" in issues or word_count < cls.MIN_OUTPUT_WORDS:
            hints.append("expand_with_missing_sections")
        if "repetitive_output" in issues:
            hints.append("summarize_and_deduplicate")
        if "hallucination_risk" in issues or "low_factuality" in issues:
            hints.append("use_tools_for_facts")
        if "poor_tool_usage" in issues and is_research:
            hints.append("invoke_web_search_first")
        if "low_usefulness" in issues:
            hints.append("add_actionable_recommendations")
        return hints
