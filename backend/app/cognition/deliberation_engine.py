"""
Deliberative reasoning engine with tree-of-thought branching.

Features:
  - hypothesis generation
  - alternative reasoning paths (ToT)
  - internal debate integration
  - uncertainty estimation
  - strategic planning memory
  - self-verification loops
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.cognition.collaboration_protocol import BlackboardEntry, CollaborationProtocol
from app.cognition.consensus_manager import ConsensusManager
from app.cognition.debate_engine import DebateEngine
from app.cognition.hypothesis_manager import HypothesisManager
from app.cognition.reasoning_memory import ReasoningMemory
from app.cognition.self_verification import SelfVerification
from app.cognition.uncertainty_engine import UncertaintyEngine
from app.execution.context_manager import ContextManager

EmitFn = Callable[..., Awaitable[None]]

# Bounded tree-of-thought limits
MAX_TOT_DEPTH = 3
MAX_BRANCHES_PER_LEVEL = 4
PRUNE_THRESHOLD = 0.25


@dataclass
class ReasoningBranch:
    id: str
    parent_id: str | None
    depth: int
    label: str
    reasoning: str
    score: float
    pruned: bool = False
    children: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parentId": self.parent_id,
            "depth": self.depth,
            "label": self.label,
            "reasoning": self.reasoning,
            "score": round(self.score, 3),
            "pruned": self.pruned,
            "children": self.children,
        }


@dataclass
class DeliberationResult:
    hypotheses: list[dict[str, Any]]
    branches: list[dict[str, Any]]
    best_branch: dict[str, Any] | None
    debate: dict[str, Any] | None
    consensus: dict[str, Any] | None
    uncertainty: dict[str, Any]
    reasoning_graph: dict[str, Any]
    verification: dict[str, Any] | None
    plan: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypotheses": self.hypotheses,
            "branches": self.branches,
            "bestBranch": self.best_branch,
            "debate": self.debate,
            "consensus": self.consensus,
            "uncertainty": self.uncertainty,
            "reasoningGraph": self.reasoning_graph,
            "verification": self.verification,
            "plan": self.plan,
        }


class DeliberationEngine:
    """Orchestrates bounded deliberative reasoning before agent execution."""

    @classmethod
    async def deliberate(
        cls,
        execution_id: str,
        objective: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        memory_hints: list[str] | None = None,
    ) -> DeliberationResult:
        reasoning_mem = ReasoningMemory(ctx)

        # 1. Hypothesis generation
        raw_hypotheses = HypothesisManager.generate(objective, memory_hints)
        hypotheses = [h.to_dict() for h in raw_hypotheses]
        for h in hypotheses:
            reasoning_mem.append_hypothesis(h)
            await emit_fn(
                execution_id,
                "hypothesis_generated",
                "cognition",
                f"Hypothesis: {h['statement'][:80]}",
                hypothesisId=h["id"],
                statement=h["statement"],
                confidence=h["confidence"],
                tags=h.get("tags", []),
            )

        # 2. Tree-of-thought branching
        branches, graph = await cls.run_tree_of_thought_async(
            objective, hypotheses, reasoning_mem, execution_id, emit_fn
        )
        branch_scores = [float(b["score"]) for b in branches if not b.get("pruned")]

        # 3. Uncertainty estimation
        uncertainty = UncertaintyEngine.assess(
            objective, hypotheses, branch_scores
        ).to_dict()
        reasoning_mem.record_uncertainty(uncertainty)
        if uncertainty["overall"] >= 0.4:
            await emit_fn(
                execution_id,
                "uncertainty_detected",
                "cognition",
                f"Uncertainty {uncertainty['overall']:.0%} — {uncertainty['recommendation']}",
                assessment=uncertainty,
            )

        # 4. Internal debate (when architecture/design or high uncertainty)
        debate_dict: dict[str, Any] | None = None
        is_arch = any(
            k in objective.lower()
            for k in HypothesisManager.ARCHITECTURE_KEYWORDS
        )
        if is_arch or uncertainty["overall"] >= 0.45:
            synthesized_hints: list[str] = []
            try:
                from app.intelligence.knowledge_reasoning_coordinator import (
                    KnowledgeReasoningCoordinator,
                )

                for item in await KnowledgeReasoningCoordinator.get_synthesized_for_debate(
                    objective, limit=3,
                ):
                    synthesized_hints.append(item.get("summary", "")[:120])
            except Exception:
                pass

            await emit_fn(
                execution_id,
                "debate_started",
                "cognition",
                "Starting multi-perspective debate",
                topic=objective[:200],
                synthesizedKnowledge=synthesized_hints,
            )
            debate = (
                DebateEngine.run_architecture_debate(objective, hypotheses)
                if is_arch
                else DebateEngine.run_generic_debate(objective)
            )
            debate_dict = debate.to_dict()
            if synthesized_hints:
                debate_dict["synthesizedKnowledge"] = synthesized_hints
            reasoning_mem.append_debate(debate_dict)

        # 5. Collaboration — blackboard + voting
        blackboard = CollaborationProtocol.create_blackboard()
        best_branch = cls._select_best_branch(branches)
        if best_branch:
            blackboard = CollaborationProtocol.post(
                blackboard,
                BlackboardEntry(
                    key="selected_path",
                    value=best_branch["label"],
                    author="deliberation_engine",
                    priority=float(best_branch["score"]),
                ),
            )

        proposals = [
            {"id": b["id"], "agent": b["label"], "weight": b["score"], "statement": b["reasoning"]}
            for b in branches
            if not b.get("pruned")
        ][:4]
        vote_result = CollaborationProtocol.vote(proposals)
        arbitration = CollaborationProtocol.arbitrate(
            vote_result, uncertainty["overall"]
        )

        # 6. Consensus
        consensus_obj = None
        consensus_dict: dict[str, Any] | None = None
        if debate_dict:
            consensus_obj = ConsensusManager.build_from_debate(
                debate_dict, best_branch, uncertainty["overall"]
            )
            consensus_dict = consensus_obj.to_dict()
            reasoning_mem.set_consensus(consensus_dict)
            await emit_fn(
                execution_id,
                "consensus_reached",
                "cognition",
                f"Consensus confidence {consensus_obj.confidence:.0%}",
                consensus=consensus_dict,
            )

        # 7. Self-verification
        verification_dict: dict[str, Any] | None = None
        verify_text = (consensus_dict or {}).get("statement") or (best_branch or {}).get("reasoning", "")
        if verify_text:
            verification = SelfVerification.verify_consensus(verify_text, objective)
            verification_dict = verification.to_dict()
            reasoning_mem.record_verification(verification_dict)
            if not verification.passed:
                await emit_fn(
                    execution_id,
                    "self_verification_failed",
                    "cognition",
                    f"Verification failed: {', '.join(verification.failure_reasons)}",
                    verification=verification_dict,
                )

        # 8. Strategic plan
        plan = cls._build_plan(objective, best_branch, consensus_dict, uncertainty)
        reasoning_mem.add_plan(plan)
        ctx.set_workflow_memory("cognitive_deliberation", DeliberationResult(
            hypotheses=hypotheses,
            branches=branches,
            best_branch=best_branch,
            debate=debate_dict,
            consensus=consensus_dict,
            uncertainty=uncertainty,
            reasoning_graph=graph,
            verification=verification_dict,
            plan=plan,
        ).to_dict())

        return DeliberationResult(
            hypotheses=hypotheses,
            branches=branches,
            best_branch=best_branch,
            debate=debate_dict,
            consensus=consensus_dict,
            uncertainty=uncertainty,
            reasoning_graph=graph,
            verification=verification_dict,
            plan=plan,
        )

    @classmethod
    async def run_tree_of_thought_async(
        cls,
        objective: str,
        hypotheses: list[dict[str, Any]],
        memory: ReasoningMemory,
        execution_id: str,
        emit_fn: EmitFn,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        all_branches: list[ReasoningBranch] = []
        frontier = cls._initial_branches(objective, hypotheses)
        all_branches.extend(frontier)

        for branch in frontier:
            await emit_fn(
                execution_id,
                "reasoning_branch_created",
                "cognition",
                f"Branch [{branch.depth}]: {branch.label}",
                branchId=branch.id,
                parentId=branch.parent_id,
                depth=branch.depth,
                label=branch.label,
                score=branch.score,
                pruned=branch.pruned,
            )
            memory.append_branch(branch.to_dict())

        # Expand depth 1..MAX_TOT_DEPTH-1 with confidence-weighted pruning
        current_frontier = [b for b in frontier if not b.pruned]
        for depth in range(1, MAX_TOT_DEPTH):
            next_frontier: list[ReasoningBranch] = []
            for parent in current_frontier:
                if parent.score < PRUNE_THRESHOLD:
                    parent.pruned = True
                    continue
                children = cls._expand_branch(parent, objective, depth)
                for child in children[:MAX_BRANCHES_PER_LEVEL]:
                    parent.children.append(child.id)
                    all_branches.append(child)
                    memory.append_branch(child.to_dict())
                    await emit_fn(
                        execution_id,
                        "reasoning_branch_created",
                        "cognition",
                        f"Branch [{child.depth}]: {child.label}",
                        branchId=child.id,
                        parentId=child.parent_id,
                        depth=child.depth,
                        label=child.label,
                        score=child.score,
                        pruned=child.pruned,
                    )
                    if child.score >= PRUNE_THRESHOLD:
                        next_frontier.append(child)
            current_frontier = next_frontier
            if not current_frontier:
                break

        # Prune low-confidence branches
        for b in all_branches:
            if b.score < PRUNE_THRESHOLD and b.depth > 0:
                b.pruned = True

        return [b.to_dict() for b in all_branches], cls._build_graph(all_branches)

    @classmethod
    def _initial_branches(
        cls, objective: str, hypotheses: list[dict[str, Any]]
    ) -> list[ReasoningBranch]:
        branches: list[ReasoningBranch] = []
        templates = [
            ("Sequential analysis", "Deep step-by-step reasoning with full context", 0.72),
            ("Parallel research", "Concurrent evidence gathering then synthesis", 0.68),
            ("Tool-grounded verification", "Anchor claims with web search and fetch", 0.75),
        ]
        if any(k in objective.lower() for k in ("security", "cyber", "monitor")):
            templates.append(
                ("Security-first architecture", "Zero-trust, SIEM integration, compliance", 0.78)
            )

        for label, reasoning, base_score in templates[:MAX_BRANCHES_PER_LEVEL]:
            hyp_boost = max(
                (float(h.get("confidence", 0.5)) for h in hypotheses),
                default=0.5,
            ) * 0.1
            branches.append(
                ReasoningBranch(
                    id=f"br-{uuid.uuid4().hex[:8]}",
                    parent_id=None,
                    depth=0,
                    label=label,
                    reasoning=f"{reasoning} for: {objective[:100]}",
                    score=min(0.95, base_score + hyp_boost),
                )
            )
        return branches

    @classmethod
    def _expand_branch(
        cls, parent: ReasoningBranch, objective: str, depth: int
    ) -> list[ReasoningBranch]:
        refinements = [
            (f"Refine: {parent.label} — evidence pass", 0.05),
            (f"Refine: {parent.label} — risk pass", -0.02),
        ]
        children: list[ReasoningBranch] = []
        for suffix, delta in refinements:
            score = max(0.0, min(0.95, parent.score + delta))
            children.append(
                ReasoningBranch(
                    id=f"br-{uuid.uuid4().hex[:8]}",
                    parent_id=parent.id,
                    depth=depth,
                    label=suffix[:80],
                    reasoning=f"{parent.reasoning[:120]} → {suffix}",
                    score=score,
                )
            )
        return children

    @classmethod
    def _build_graph(cls, branches: list[ReasoningBranch]) -> dict[str, Any]:
        nodes = [b.to_dict() for b in branches]
        edges = [
            {"from": b.parent_id, "to": b.id}
            for b in branches
            if b.parent_id
        ]
        active = [b for b in branches if not b.pruned]
        return {
            "nodes": nodes,
            "edges": edges,
            "maxDepth": MAX_TOT_DEPTH,
            "activeCount": len(active),
            "prunedCount": len(branches) - len(active),
        }

    @classmethod
    def _select_best_branch(cls, branches: list[dict[str, Any]]) -> dict[str, Any] | None:
        active = [b for b in branches if not b.get("pruned")]
        if not active:
            return None
        return max(active, key=lambda b: float(b.get("score", 0)))

    @classmethod
    def _build_plan(
        cls,
        objective: str,
        best_branch: dict[str, Any] | None,
        consensus: dict[str, Any] | None,
        uncertainty: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "id": f"plan-{uuid.uuid4().hex[:8]}",
            "objective": objective[:500],
            "approach": (best_branch or {}).get("label", "default"),
            "guidance": (consensus or {}).get("statement", (best_branch or {}).get("reasoning", ""))[:600],
            "uncertaintyLevel": uncertainty.get("overall", 0),
            "phases": [
                "deliberate_and_plan",
                "execute_with_tools",
                "verify_and_synthesize",
                "reflect_and_store",
            ],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
