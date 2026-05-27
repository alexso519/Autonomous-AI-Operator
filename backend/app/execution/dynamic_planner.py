"""
Dynamic task decomposition and autonomous replanning without workflow restart.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Awaitable, Callable

from app.config.locale import t
from app.execution.adaptive_graph import AdaptiveExecutionGraph, GraphMutation
from app.execution.context_manager import ContextManager
from app.execution.hierarchical_memory import HierarchicalMemory
from app.execution.reflection_service import ReflectionFinding, ReplanningService
from app.execution.strategy_selector import ExecutionStrategy, StrategySelector

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class DynamicPlanner:
    """Runtime planner: strategies, decomposition, and adaptive replanning."""

    MAX_RUNTIME_SPAWNED = 2

    SUBGOAL_PATTERN = re.compile(
        r"(?i)(?:step\s*\d+|phase\s*\d+|first|second|then|also|additionally)[:\s]+([^.!\n]{20,120})"
    )
    TOOL_JSON_PATTERN = re.compile(r'"tool_name"\s*:')

    @classmethod
    async def initialize_execution(
        cls,
        execution_id: str,
        objective: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        resume: bool = False,
    ) -> tuple[AdaptiveExecutionGraph, ExecutionStrategy]:
        graph = AdaptiveExecutionGraph(nodes, edges)
        memory = HierarchicalMemory(ctx)
        memory.set_working("objective", objective)

        existing = ctx.get_workflow_memory("selected_strategy")
        if resume and existing:
            strategy = ExecutionStrategy(
                id=existing.get("id", "resumed"),
                label=existing.get("label", "Resumed"),
                approach=existing.get("approach", ""),
                quality_estimate=existing.get("qualityEstimate", 0.7),
                tool_availability=existing.get("toolAvailability", 0.5),
                cost_estimate=existing.get("costEstimate", 0.5),
                hallucination_risk=existing.get("hallucinationRisk", 0.3),
                parallel_friendly=existing.get("parallelFriendly", False),
            )
            ctx.set_workflow_memory("adaptive_graph_enabled", True)
            return graph, strategy

        complexity = str(ctx.get_workflow_memory("task_complexity") or "moderate")

        try:
            from app.intelligence.knowledge_planner import KnowledgePlanner

            kr_plan = await KnowledgePlanner.plan_with_knowledge(
                objective,
                execution_id=execution_id,
                ctx_data=ctx.get_workflow_memory("cognitive_deliberation") or {},
                emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("knowledge_planning", kr_plan)
            block = kr_plan.get("contextBlock") or ""
            if block:
                ctx.set_context((ctx.get_context() or "") + f"\n\n{block}")
        except Exception as exc:
            logger.debug("Knowledge planning skipped: %s", exc)

        candidates = StrategySelector.generate_candidates(objective, nodes, complexity)
        strategy = StrategySelector.select_best(candidates, ctx, objective=objective)
        memory.record_strategy(strategy.to_dict())
        ctx.set_workflow_memory("selected_strategy", strategy.to_dict())
        ctx.set_workflow_memory("strategy_candidates", [c.to_dict() for c in candidates])
        ctx.set_workflow_memory("adaptive_graph_enabled", True)
        ctx.set_workflow_memory("parallel_execution", strategy.parallel_friendly)

        await emit_fn(
            execution_id,
            "strategy_selected",
            "system",
            t("selected_strategy", label=strategy.label, score=f"{strategy.composite_score:.0%}"),
            strategyId=strategy.id,
            strategyLabel=strategy.label,
            approach=strategy.approach,
            compositeScore=strategy.composite_score,
            candidates=[c.to_dict() for c in candidates],
            parallelFriendly=strategy.parallel_friendly,
        )

        decomposed = cls._decompose_objective(objective, nodes, graph, ctx)
        if decomposed:
            mutation = graph.mutations[-1] if graph.mutations else None
            await emit_fn(
                execution_id,
                "graph_mutated",
                "system",
                t("decomposed_subgoals", count=len(decomposed)),
                mutation=mutation.to_dict() if mutation else {},
                nodeIds=[n["id"] for n in decomposed],
                action="decompose",
            )

        return graph, strategy

    @classmethod
    def _decompose_objective(
        cls,
        objective: str,
        nodes: list[dict[str, Any]],
        graph: AdaptiveExecutionGraph,
        ctx: ContextManager,
    ) -> list[dict[str, Any]]:
        """
        Split complex objectives into child agents when the graph is small
        and the objective implies multiple phases.
        """
        agent_nodes = [n for n in nodes if n.get("type") != "approval"]
        if len(agent_nodes) >= 4:
            return []

        subgoals = cls._extract_subgoals(objective)
        if len(subgoals) < 2:
            return []

        parent = agent_nodes[-1] if agent_nodes else None
        if not parent:
            return []

        spawned: list[dict[str, Any]] = []
        for i, subgoal in enumerate(subgoals[:3]):
            child = graph.spawn_child_agent(parent, subgoal, suffix=str(i))
            spawned.append(child)

        ctx.set_workflow_memory("decomposed_subgoals", [n["id"] for n in spawned])
        HierarchicalMemory(ctx).append_replan_event(
            {"action": "decompose", "subgoals": len(spawned)}
        )
        return spawned

    @classmethod
    def _looks_like_tool_or_json(cls, text: str) -> bool:
        """True when text is a tool request payload, not natural-language subgoals."""
        stripped = text.strip()
        if not stripped:
            return True
        if cls.TOOL_JSON_PATTERN.search(stripped):
            return True
        if stripped.startswith("{") and "input_data" in stripped:
            return True
        return False

    @classmethod
    def _is_valid_subgoal(cls, text: str) -> bool:
        if len(text.strip()) <= 20:
            return False
        if cls._looks_like_tool_or_json(text):
            return False
        if "{" in text or "}" in text:
            return False
        if '"' in text and ":" in text:
            return False
        if text.lstrip().startswith("#"):
            return False
        if text.count(".") >= 2 and len(text) > 120:
            return False
        return True

    @classmethod
    def _looks_like_completed_report(cls, text: str) -> bool:
        """Agent already produced a structured answer — do not decompose further."""
        stripped = text.strip()
        if not stripped:
            return False
        if re.search(r"^#{1,3}\s+\S", stripped, re.MULTILINE):
            return True
        if re.search(r"^\*\*[^*]+\*\*", stripped, re.MULTILINE):
            return True
        if len(re.findall(r"\w+", stripped)) >= 80:
            return True
        return False

    @classmethod
    def _extract_explicit_subgoals(cls, text: str) -> list[str]:
        """Match only explicit step/phase markers — safe for agent outputs."""
        if cls._looks_like_tool_or_json(text):
            return []

        found: list[str] = []
        for match in cls.SUBGOAL_PATTERN.finditer(text):
            candidate = match.group(1).strip()
            if cls._is_valid_subgoal(candidate):
                found.append(candidate)
        return found[:3]

    @classmethod
    def _extract_subgoals(cls, objective: str) -> list[str]:
        """Decompose workflow objectives (may split on 'and' for multi-part goals)."""
        if cls._looks_like_tool_or_json(objective):
            return []

        found = cls._extract_explicit_subgoals(objective)
        if len(found) >= 2:
            return found

        if " and " in objective.lower() and len(objective) > 60:
            parts = re.split(r"\s+and\s+", objective, maxsplit=2, flags=re.IGNORECASE)
            if len(parts) >= 2:
                return [
                    p.strip() for p in parts
                    if cls._is_valid_subgoal(p)
                ][:3]
        return []

    @classmethod
    async def replan_remaining(
        cls,
        execution_id: str,
        graph: AdaptiveExecutionGraph,
        node: dict[str, Any],
        finding: ReflectionFinding,
        sorted_nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_index: int,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        """
        Rebuild remaining plan by injecting recovery nodes — preserves completed work.
        """
        await emit_fn(
            execution_id,
            "replanning_started",
            "system",
            t("replanning_after", agent=finding.agent_name, reason=finding.reason),
            nodeId=finding.node_id,
            issues=finding.issues,
            qualityScore=finding.quality_score,
            retryType=finding.retry_type,
        )

        memory = HierarchicalMemory(ctx)
        memory.append_replan_event(
            {
                "nodeId": finding.node_id,
                "action": finding.action,
                "issues": finding.issues,
            }
        )

        objective = str(ctx.get_workflow_memory("objective") or ctx.get_context() or "")
        try:
            from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator

            await IntelligenceOrchestrator.retrieve_for_replanning(
                execution_id,
                objective,
                finding.reason or "",
                emit_fn,
            )
        except Exception as exc:
            logger.debug("Semantic replanning retrieval skipped: %s", exc)

        updated = ReplanningService.insert_recovery_steps(
            sorted_nodes=sorted_nodes,
            edges=edges,
            node_index=node_index,
            node=node,
            finding=finding,
            ctx=ctx,
        )

        new_node_ids = [
            n["id"] for n in updated
            if n["id"] not in graph.nodes
        ]
        new_nodes = [n for n in updated if n["id"] in new_node_ids]
        if new_nodes:
            mutation = graph.insert_nodes(
                new_nodes,
                after_node_id=finding.node_id,
                reason="recovery_replan",
            )
            await emit_fn(
                execution_id,
                "graph_mutated",
                "system",
                t("graph_updated", count=len(new_nodes)),
                mutation=mutation.to_dict(),
                nodeIds=new_node_ids,
                action="recovery_insert",
            )

        await emit_fn(
            execution_id,
            "replanning_completed",
            "system",
            t("replanning_complete", count=len(updated)),
            nodeId=finding.node_id,
            totalNodes=len(updated),
            injectedNodes=new_node_ids,
        )

        return updated

    @classmethod
    async def maybe_spawn_subgoal_agents(
        cls,
        execution_id: str,
        graph: AdaptiveExecutionGraph,
        node: dict[str, Any],
        output: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        """Runtime decomposition when agent output lists explicit follow-up subgoals."""
        if node.get("data", {}).get("executionMetadata", {}).get("subgoal"):
            return []

        if cls._looks_like_tool_or_json(output):
            return []

        if cls._looks_like_completed_report(output):
            return []

        spawned_count = int(ctx.get_workflow_memory("runtime_spawned_count") or 0)
        if spawned_count >= cls.MAX_RUNTIME_SPAWNED:
            return []

        subgoals = cls._extract_explicit_subgoals(output)
        if len(subgoals) < 2:
            return []

        spawned: list[dict[str, Any]] = []
        for i, subgoal in enumerate(subgoals[:2]):
            child = graph.spawn_child_agent(node, subgoal, suffix=f"rt{i}")
            spawned.append(child)
            await emit_fn(
                execution_id,
                "child_agent_spawned",
                "system",
                t("spawned_child_agent", subgoal=subgoal[:80]),
                parentNodeId=node.get("id"),
                childNodeId=child["id"],
                subgoal=subgoal,
            )
        ctx.set_workflow_memory(
            "runtime_spawned_count",
            spawned_count + len(spawned),
        )
        return spawned
