"""
Retrieval planner — decides which memory types to retrieve.

Role-aware, task-aware, synthesis and retry strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RetrievalStrategy(str, Enum):
    STANDARD = "standard"
    SYNTHESIS = "synthesis"
    RETRY_RECOVERY = "retry_recovery"
    RESEARCH = "research"
    COGNITIVE = "cognitive"
    MINIMAL = "minimal"


@dataclass(frozen=True)
class RetrievalPlan:
    """Deterministic retrieval plan for a context assembly request."""

    strategy: RetrievalStrategy
    namespaces: list[str]
    limits: dict[str, int]
    include_cognitive: bool = False
    deprioritize_retries: bool = True
    prioritize_evidence: bool = True
    reason: str = ""
    replay_token: str = ""


@dataclass
class AssemblyRequest:
    """Input for context assembly."""

    model_tier: str = "standard"
    is_synthesis: bool = False
    is_retry: bool = False
    agent_name: str = ""
    agent_role: str = ""
    task_complexity: str = "moderate"
    confidence_score: float | None = None
    research_mode: bool = False
    cognitive_hints: list[str] = field(default_factory=list)
    include_tool_plan: bool = True
    tool_plan_block: str = ""


# Namespace identifiers used by MemoryCoordinator
NS_WORKFLOW = "workflow_outputs"
NS_HIERARCHICAL = "hierarchical"
NS_RESEARCH = "research"
NS_REASONING = "reasoning"
NS_COGNITIVE = "cognitive"
NS_SHARED = "shared"
NS_TOOL = "tool_results"
NS_EVIDENCE = "evidence"
NS_SEMANTIC = "semantic"


class RetrievalPlanner:
    """Plan memory retrieval based on execution context."""

    DEFAULT_LIMITS: dict[str, int] = {
        NS_WORKFLOW: 10,
        NS_HIERARCHICAL: 5,
        NS_RESEARCH: 8,
        NS_REASONING: 5,
        NS_COGNITIVE: 3,
        NS_SHARED: 5,
        NS_TOOL: 6,
        NS_EVIDENCE: 8,
        NS_SEMANTIC: 4,
    }

    @classmethod
    def plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        """Select retrieval strategy and namespaces."""
        if request.is_synthesis:
            return cls._synthesis_plan(request)
        if request.is_retry:
            return cls._retry_plan(request)
        if request.research_mode:
            return cls._research_plan(request)
        if request.confidence_score is not None and request.confidence_score < 0.45:
            return cls._low_confidence_plan(request)
        return cls._standard_plan(request)

    @classmethod
    def _standard_plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        namespaces = [NS_WORKFLOW, NS_TOOL, NS_SHARED, NS_HIERARCHICAL]
        if cls._is_reasoning_role(request):
            namespaces.append(NS_REASONING)
        limits = {ns: cls.DEFAULT_LIMITS[ns] for ns in namespaces}
        return RetrievalPlan(
            strategy=RetrievalStrategy.STANDARD,
            namespaces=namespaces,
            limits=limits,
            reason=f"standard tier={request.model_tier}",
            replay_token=cls._replay_token(request, RetrievalStrategy.STANDARD),
        )

    @classmethod
    def _synthesis_plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        namespaces = [
            NS_EVIDENCE,
            NS_TOOL,
            NS_RESEARCH,
            NS_WORKFLOW,
            NS_SHARED,
            NS_HIERARCHICAL,
        ]
        limits = {
            NS_EVIDENCE: 10,
            NS_TOOL: 4,
            NS_RESEARCH: 6,
            NS_WORKFLOW: 4,
            NS_SHARED: 3,
            NS_HIERARCHICAL: 3,
        }
        return RetrievalPlan(
            strategy=RetrievalStrategy.SYNTHESIS,
            namespaces=namespaces,
            limits=limits,
            deprioritize_retries=True,
            prioritize_evidence=True,
            reason="synthesis: prioritize grounded evidence",
            replay_token=cls._replay_token(request, RetrievalStrategy.SYNTHESIS),
        )

    @classmethod
    def _retry_plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        namespaces = [NS_WORKFLOW, NS_TOOL, NS_EVIDENCE, NS_SHARED, NS_SEMANTIC]
        limits = {
            NS_WORKFLOW: 3,
            NS_TOOL: 4,
            NS_EVIDENCE: 6,
            NS_SHARED: 2,
            NS_SEMANTIC: 3,
        }
        return RetrievalPlan(
            strategy=RetrievalStrategy.RETRY_RECOVERY,
            namespaces=namespaces,
            limits=limits,
            deprioritize_retries=False,
            prioritize_evidence=True,
            reason="retry recovery: recent tools and evidence only",
            replay_token=cls._replay_token(request, RetrievalStrategy.RETRY_RECOVERY),
        )

    @classmethod
    def _research_plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        namespaces = [
            NS_RESEARCH,
            NS_EVIDENCE,
            NS_TOOL,
            NS_WORKFLOW,
            NS_HIERARCHICAL,
        ]
        limits = {ns: cls.DEFAULT_LIMITS[ns] for ns in namespaces}
        limits[NS_RESEARCH] = 10
        limits[NS_EVIDENCE] = 10
        return RetrievalPlan(
            strategy=RetrievalStrategy.RESEARCH,
            namespaces=namespaces,
            limits=limits,
            prioritize_evidence=True,
            reason="research mode active",
            replay_token=cls._replay_token(request, RetrievalStrategy.RESEARCH),
        )

    @classmethod
    def _low_confidence_plan(cls, request: AssemblyRequest) -> RetrievalPlan:
        namespaces = [NS_EVIDENCE, NS_TOOL, NS_RESEARCH, NS_WORKFLOW]
        limits = {
            NS_EVIDENCE: 10,
            NS_TOOL: 6,
            NS_RESEARCH: 6,
            NS_WORKFLOW: 2,
        }
        return RetrievalPlan(
            strategy=RetrievalStrategy.STANDARD,
            namespaces=namespaces,
            limits=limits,
            prioritize_evidence=True,
            reason=f"low confidence={request.confidence_score:.2f}",
            replay_token=cls._replay_token(request, RetrievalStrategy.STANDARD),
        )

    @classmethod
    def _is_reasoning_role(cls, request: AssemblyRequest) -> bool:
        name = request.agent_name.lower()
        role = request.agent_role.lower()
        tier = request.model_tier.lower()
        return (
            tier in ("reasoning", "heavy")
            or "plan" in name
            or "deliber" in name
            or "cognit" in name
            or "reason" in role
        )

    @classmethod
    def _replay_token(cls, request: AssemblyRequest, strategy: RetrievalStrategy) -> str:
        parts = [
            strategy.value,
            request.model_tier,
            "syn" if request.is_synthesis else "",
            "retry" if request.is_retry else "",
            request.agent_name[:20],
        ]
        return "|".join(p for p in parts if p)

    @classmethod
    def cognitive_plan(cls, objective: str, limit: int = 3) -> RetrievalPlan:
        """Plan for cross-execution cognitive memory retrieval."""
        return RetrievalPlan(
            strategy=RetrievalStrategy.COGNITIVE,
            namespaces=[NS_COGNITIVE],
            limits={NS_COGNITIVE: limit},
            include_cognitive=True,
            reason=f"cognitive retrieval for objective hash",
            replay_token=f"cognitive|{objective[:40]}",
        )
