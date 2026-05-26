"""
Multi-strategy reasoning: generate candidate approaches and select the best.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.execution.context_manager import ContextManager
from app.tools.tool_registry import tool_registry


@dataclass
class ExecutionStrategy:
    id: str
    label: str
    approach: str
    quality_estimate: float
    tool_availability: float
    cost_estimate: float
    hallucination_risk: float
    parallel_friendly: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def composite_score(self) -> float:
        return (
            self.quality_estimate * 0.35
            + self.tool_availability * 0.25
            + (1.0 - self.cost_estimate) * 0.15
            + (1.0 - self.hallucination_risk) * 0.25
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "approach": self.approach,
            "qualityEstimate": round(self.quality_estimate, 3),
            "toolAvailability": round(self.tool_availability, 3),
            "costEstimate": round(self.cost_estimate, 3),
            "hallucinationRisk": round(self.hallucination_risk, 3),
            "compositeScore": round(self.composite_score, 3),
            "parallelFriendly": self.parallel_friendly,
            "reasons": self.reasons,
        }


class StrategySelector:
    """Generate and rank execution strategies for a workflow objective."""

    RESEARCH_KEYWORDS = (
        "research", "analyze", "investigate", "compare", "report",
        "strategy", "market", "benchmark",
    )

    @classmethod
    def generate_candidates(
        cls,
        objective: str,
        nodes: list[dict[str, Any]],
        complexity: str = "moderate",
    ) -> list[ExecutionStrategy]:
        objective_lower = objective.lower()
        is_research = any(k in objective_lower for k in cls.RESEARCH_KEYWORDS)
        agent_count = len([n for n in nodes if n.get("type") != "approval"])
        available_tools = len(tool_registry.list())
        tool_avail = min(1.0, available_tools / 8.0)

        strategies: list[ExecutionStrategy] = []

        strategies.append(
            ExecutionStrategy(
                id=f"seq-{uuid.uuid4().hex[:8]}",
                label="Sequential Deep Dive",
                approach="Execute agents one-by-one with full context accumulation.",
                quality_estimate=0.82 if complexity in ("complex", "high") else 0.75,
                tool_availability=tool_avail,
                cost_estimate=0.7,
                hallucination_risk=0.25 if is_research else 0.35,
                parallel_friendly=False,
                reasons=["Best for complex reasoning chains"],
            )
        )

        if agent_count >= 2 or is_research:
            strategies.append(
                ExecutionStrategy(
                    id=f"par-{uuid.uuid4().hex[:8]}",
                    label="Parallel Research Sweep",
                    approach="Run independent research agents concurrently, then synthesize.",
                    quality_estimate=0.78,
                    tool_availability=min(1.0, tool_avail + 0.1),
                    cost_estimate=0.55,
                    hallucination_risk=0.3,
                    parallel_friendly=True,
                    reasons=["Faster evidence gathering", "Good for multi-source research"],
                )
            )

        strategies.append(
            ExecutionStrategy(
                id=f"tool-{uuid.uuid4().hex[:8]}",
                label="Tool-First Grounding",
                approach="Prioritize web_search and webpage_fetch before analysis agents.",
                quality_estimate=0.85 if is_research else 0.7,
                tool_availability=min(1.0, tool_avail + 0.15),
                cost_estimate=0.65,
                hallucination_risk=0.15 if is_research else 0.4,
                parallel_friendly=is_research,
                reasons=["Minimizes unsupported claims", "Research tasks benefit most"],
            )
        )

        if complexity in ("simple", "low"):
            strategies.append(
                ExecutionStrategy(
                    id=f"lite-{uuid.uuid4().hex[:8]}",
                    label="Lightweight Fast Path",
                    approach="Minimal agents, low token cost, quick synthesis.",
                    quality_estimate=0.65,
                    tool_availability=tool_avail * 0.8,
                    cost_estimate=0.25,
                    hallucination_risk=0.45,
                    parallel_friendly=False,
                    reasons=["Simple tasks don't need heavy orchestration"],
                )
            )

        return strategies

    @classmethod
    def select_best(
        cls,
        candidates: list[ExecutionStrategy],
        ctx: ContextManager | None = None,
        *,
        objective: str = "",
    ) -> ExecutionStrategy:
        if not candidates:
            return ExecutionStrategy(
                id="default",
                label="Default",
                approach="Standard sequential execution",
                quality_estimate=0.7,
                tool_availability=0.5,
                cost_estimate=0.5,
                hallucination_risk=0.35,
            )

        boosted = list(candidates)

        if ctx:
            learned_ctx = ctx.get_workflow_memory("intelligence_context") or {}
            hints = learned_ctx.get("planningHints") or {}
            learned_list = hints.get("learnedStrategies") or []
            cls._apply_learned_boost(boosted, learned_list)

            kr = ctx.get_workflow_memory("knowledge_planning") or {}
            structure = kr.get("recommendedStructure", "")
            if structure == "multi_entity_research":
                for s in boosted:
                    if "research" in s.label.lower() or "tool" in s.label.lower():
                        s.quality_estimate = min(1.0, s.quality_estimate + 0.06)
                        s.reasons.append("Boosted by knowledge graph structure")
            elif structure == "reuse_prior_pattern":
                for s in boosted:
                    if "sequential" in s.label.lower():
                        s.quality_estimate = min(1.0, s.quality_estimate + 0.05)
                        s.reasons.append("Boosted by prior successful knowledge structure")

            complexity = str(ctx.get_workflow_memory("task_complexity") or "moderate")
            if complexity in ("complex", "high"):
                research = [
                    s for s in boosted
                    if "research" in s.label.lower() or "tool" in s.label.lower()
                ]
                if research:
                    return max(research, key=lambda s: s.composite_score)

        return max(boosted, key=lambda s: s.composite_score)

    @classmethod
    def _apply_learned_boost(
        cls,
        candidates: list[ExecutionStrategy],
        learned: list[dict[str, Any]],
    ) -> None:
        if not learned:
            return
        best_label = learned[0].get("strategyLabel", "").lower()
        for c in candidates:
            if best_label and best_label in c.label.lower():
                c.quality_estimate = min(1.0, c.quality_estimate + 0.08)
                c.reasons.append("Boosted by learned strategy history")
